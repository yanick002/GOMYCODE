"""Application web Portefeuille BRVM.

Lancement local :  python main.py      (http://localhost:8080)
Sur Render     :  python main.py      (Render fournit PORT)
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import agent
import base
import config
import marche
import portefeuille

ICI = Path(__file__).parent
journal = logging.getLogger("portefeuille")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
    # Reference gardee : une tache sans reference peut etre ramassee en route.
    app.state.reveil = asyncio.create_task(marche.reveiller())
    yield
    await app.state.http.aclose()


app = FastAPI(title="Portefeuille BRVM", lifespan=cycle_de_vie, docs_url=None, redoc_url=None)


# ------------------------------------------------------------------ erreurs

@app.exception_handler(base.NonAutorise)
async def _non_autorise(request: Request, e: base.NonAutorise):
    return JSONResponse({"detail": str(e)}, status_code=401)


@app.exception_handler(base.ErreurBase)
async def _erreur_base(request: Request, e: base.ErreurBase):
    journal.error("Supabase : %s", e)
    return JSONResponse({"detail": "La base de donnees a refuse l'operation."}, status_code=502)


@app.exception_handler(marche.MarcheIndisponible)
async def _marche(request: Request, e: marche.MarcheIndisponible):
    return JSONResponse({"detail": str(e)}, status_code=503)


# ------------------------------------------------------------------ compte

async def utilisateur_courant(request: Request, authorization: str = Header(default="")) -> base.Utilisateur:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Connexion requise.")
    return await base.utilisateur(request.app.state.http, authorization[len("Bearer "):])


# ------------------------------------------------------------------ modeles

class Position(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z0-9-]{2,10}$")
    quantite: float = Field(gt=0, le=1e9)
    prix_achat: float = Field(ge=0, le=1e9)


class Liquidites(BaseModel):
    montant: float = Field(ge=0, le=1e13)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    historique: list[Message] = Field(default_factory=list, max_length=20)


# ------------------------------------------------------------------ pages

@app.get("/")
async def accueil():
    return FileResponse(ICI / "static" / "index.html")


@app.get("/health")
async def sante():
    return {"status": "ok"}


@app.get("/api/config")
async def configuration_publique():
    """Ce que la page a besoin de connaitre. Rien de secret : la cle Supabase
    publishable est faite pour etre dans le navigateur."""
    return {"supabase_url": config.SUPABASE_URL,
            "supabase_key": config.SUPABASE_PUBLISHABLE_KEY,
            "modeles": config.LLM_MODELS}


@app.get("/api/tickers")
async def tickers():
    """Liste des titres et indices, pour l'autocompletion du formulaire."""
    async with marche.session_mcp() as session:
        _, texte = await marche.appeler(session, "get_all_tickers", {})
    return json.loads(texte)


# ------------------------------------------------------------------ portefeuille

async def _valorisation(http: httpx.AsyncClient, u: base.Utilisateur, session=None) -> dict:
    positions, liquidites = await asyncio.gather(base.lire_positions(http, u), base.lire_liquidites(http, u))
    tickers = [p["ticker"] for p in positions]

    async def avec(session):
        return await asyncio.gather(marche.cours(session, tickers), marche.date_seance(session))

    if session is not None:
        indicateurs, date = await avec(session)
        indisponible = False
    else:
        # Le MCP en panne n'empeche pas de voir son portefeuille : les lignes
        # s'affichent sans cours, et la page le signale.
        try:
            async with marche.session_mcp() as s:
                indicateurs, date = await avec(s)
            indisponible = False
        except marche.MarcheIndisponible:
            indicateurs, date, indisponible = {}, None, True

    return {"date_seance": date, "marche_indisponible": indisponible,
            **portefeuille.valoriser(positions, liquidites, indicateurs)}


@app.get("/api/portefeuille")
async def voir_portefeuille(request: Request, u: base.Utilisateur = Depends(utilisateur_courant)):
    return await _valorisation(request.app.state.http, u)


@app.put("/api/positions")
async def enregistrer_position(p: Position, request: Request,
                               u: base.Utilisateur = Depends(utilisateur_courant)):
    ticker = p.ticker.upper()
    # On n'enregistre qu'un titre que le MCP connait : une faute de frappe
    # donnerait sinon une ligne a jamais sans cours.
    async with marche.session_mcp() as session:
        connus = await marche.cours(session, [ticker])
    if ticker not in connus:
        raise HTTPException(400, "Titre inconnu a la BRVM : %s" % ticker)
    await base.enregistrer_position(request.app.state.http, u, ticker, p.quantite, p.prix_achat)
    return {"ok": True}


@app.delete("/api/positions/{position_id}")
async def supprimer_position(position_id: int, request: Request,
                             u: base.Utilisateur = Depends(utilisateur_courant)):
    await base.supprimer_position(request.app.state.http, u, position_id)
    return {"ok": True}


@app.put("/api/liquidites")
async def enregistrer_liquidites(l: Liquidites, request: Request,
                                 u: base.Utilisateur = Depends(utilisateur_courant)):
    await base.enregistrer_liquidites(request.app.state.http, u, l.montant)
    return {"ok": True}


# ------------------------------------------------------------------ questions

@app.post("/api/chat")
async def discuter(q: Question, request: Request, u: base.Utilisateur = Depends(utilisateur_courant)):
    """Reponse en flux NDJSON : une ligne JSON par evenement (outil appele,
    reponse, erreur), pour que la page montre les appels au fur et a mesure."""
    http = request.app.state.http
    historique = [m.model_dump() for m in q.historique]
    file: asyncio.Queue = asyncio.Queue()

    # La session MCP vit dans une tache a part, qui la cree et la ferme
    # elle-meme. Un generateur qui la tiendrait ouverte entre deux yield
    # casserait si le navigateur coupe la connexion en cours de route.
    async def travail():
        try:
            async with marche.session_mcp() as session:
                valorisation = await _valorisation(http, u, session)
                async for evenement in agent.repondre(q.question, historique, valorisation,
                                                      valorisation["date_seance"], session):
                    await file.put(evenement)
        except (marche.MarcheIndisponible, agent.ErreurModele, base.NonAutorise) as e:
            await file.put({"type": "erreur", "message": str(e)})
        except Exception:
            journal.exception("Echec de /api/chat")
            await file.put({"type": "erreur", "message": "Erreur interne, reessaie."})
        finally:
            await file.put(None)

    async def flux():
        tache = asyncio.create_task(travail())
        try:
            while (evenement := await file.get()) is not None:
                yield json.dumps(evenement, ensure_ascii=False) + "\n"
        finally:
            tache.cancel()

    return StreamingResponse(flux(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
