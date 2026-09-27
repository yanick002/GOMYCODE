"""Connexion au serveur MCP BRVM : cours du jour et outils pour le modele.

Le MCP est la seule porte d'entree vers les donnees de marche. L'application
ne lit pas les CSV elle-meme : un changement de source ne touchera que le MCP.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import config

journal = logging.getLogger("portefeuille.marche")

# Le service gratuit de Render dort apres 15 min : le reveil prend jusqu'a une
# minute, d'ou un delai large.
DELAI = httpx.Timeout(90.0)


class MarcheIndisponible(Exception):
    pass


def _cause(e: BaseException) -> BaseException:
    """Le client MCP tourne dans un groupe de taches anyio, qui emballe toute
    erreur du bloc appelant dans un ExceptionGroup. On rend l'erreur d'origine."""
    if isinstance(e, BaseExceptionGroup):
        for sous in e.exceptions:
            c = _cause(sous)
            if not isinstance(c, asyncio.CancelledError):
                return c
    return e


@asynccontextmanager
async def session_mcp():
    connecte = False
    try:
        async with httpx.AsyncClient(timeout=DELAI) as http:
            async with streamable_http_client(config.MCP_URL, http_client=http) as (lecture, ecriture, _):
                async with ClientSession(lecture, ecriture) as session:
                    await session.initialize()
                    connecte = True
                    yield session
    except Exception as e:
        # Seul un echec de connexion devient MarcheIndisponible. Une erreur levee
        # par le code appelant, une fois connecte, remonte deballee.
        if connecte:
            raise _cause(e)
        journal.warning("MCP injoignable (%s) : %r", config.MCP_URL, e)
        raise MarcheIndisponible(
            "Les donnees de marche sont injoignables pour l'instant. Reessaie dans une minute."
        ) from e


async def reveiller() -> None:
    """Appel sans consequence au demarrage, pour que le MCP soit eveille a la
    premiere question."""
    sante = config.MCP_URL.rsplit("/mcp", 1)[0] + "/health"
    try:
        async with httpx.AsyncClient(timeout=DELAI) as http:
            await http.get(sante)
    except Exception:
        pass


def texte_resultat(resultat) -> str:
    return "\n".join(c.text for c in resultat.content if getattr(c, "text", None))


async def appeler(session: ClientSession, nom: str, arguments: dict) -> tuple[bool, str]:
    """Rend (succes, texte). Une erreur d'outil devient un texte lisible par le
    modele plutot qu'une exception : il peut alors corriger son appel."""
    try:
        resultat = await session.call_tool(nom, arguments)
    except Exception as e:
        return False, "Erreur en appelant %s : %s" % (nom, e)
    return (not resultat.isError), texte_resultat(resultat)


async def cours(session: ClientSession, tickers: list[str]) -> dict[str, dict]:
    """Indicateurs du jour pour chaque ticker demande. Un ticker inconnu est absent du resultat."""
    async def un(t):
        ok, texte = await appeler(session, "get_indicators", {"ticker": t})
        if not ok:
            return t, None
        try:
            return t, json.loads(texte)
        except ValueError:
            return t, None

    paires = await asyncio.gather(*[un(t) for t in tickers])
    return {t: d for t, d in paires if d}


async def historiques(session: ClientSession, tickers: list[str], seances: int) -> dict[str, list[tuple[str, float]]]:
    """Cours de cloture des dernieres seances : {ticker: [(date, cloture), ...]}, du plus ancien au plus recent."""
    async def un(t):
        ok, texte = await appeler(session, "get_ticker_data", {"ticker": t, "period": "daily", "limit": seances})
        if not ok:
            return t, []
        points = []
        for ligne in texte.strip().splitlines()[1:]:          # entete : Date,Open,High,Low,Close,Volume
            champs = ligne.split(",")
            try:
                points.append((champs[0], float(champs[4])))
            except (IndexError, ValueError):
                continue
        return t, points

    return dict(await asyncio.gather(*[un(t) for t in tickers]))


async def date_seance(session: ClientSession) -> str | None:
    """Date de la derniere seance, lue sur le BRVM Composite."""
    ok, texte = await appeler(session, "get_ticker_data", {"ticker": "BRVMC", "period": "daily", "limit": 1})
    if not ok:
        return None
    lignes = texte.strip().splitlines()
    return lignes[-1].split(",")[0] if len(lignes) > 1 else None


async def outils_pour_modele(session: ClientSession) -> list[dict]:
    """Les outils du MCP au format « function calling » compatible OpenAI."""
    outils = (await session.list_tools()).tools
    return [
        {
            "type": "function",
            "function": {
                "name": o.name,
                "description": (o.description or "").strip(),
                "parameters": o.inputSchema or {"type": "object", "properties": {}},
            },
        }
        for o in outils
    ]
