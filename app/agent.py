"""Boucle question -> modele -> outils MCP -> reponse.

Fonctionne avec tout fournisseur compatible OpenAI (/chat/completions avec
« tools ») : Groq, OpenRouter, Together, Ollama, vLLM... Le modele choisi doit
savoir appeler des outils (function calling), sinon il repond sans donnees.
"""
import asyncio
import json
import re

import httpx
from mcp import ClientSession

import config
import marche

MAX_TOURS = 6            # allers-retours modele <-> outils avant de forcer la reponse
MAX_RESULTAT = 12_000    # caracteres d'un resultat d'outil renvoyes au modele
DELAI = httpx.Timeout(120.0)

SYSTEME = """Tu es l'assistant d'analyse d'une application de suivi de portefeuille \
sur la BRVM (Bourse regionale des valeurs mobilieres, Afrique de l'Ouest). \
Tu reponds en francais, clairement et sans remplissage.

Donnees
- Le portefeuille de l'utilisateur est ci-dessous, deja valorise aux cours de la \
seance du {date}. Montants en FCFA. Ces chiffres sont calcules par l'application : \
reprends-les tels quels, ne les recalcule pas.
- Pour toute autre donnee de marche (cours, RSI, variations, historique, autres \
titres, indices), utilise les outils. N'invente jamais un chiffre : si aucun outil \
ne le fournit, dis que tu ne l'as pas.
- Dans les indicateurs, les variations sont en decimales (0.05 = +5 %).
- Quand il te faut plusieurs outils, appelle-les ensemble dans le meme tour. \
Ne redemande pas une donnee deja obtenue : le quota d'appels est limite.

Reponse
- Cite les chiffres sur lesquels tu t'appuies.
- Si la question depasse ces donnees (actualite, resultats financiers, dividendes \
a venir), dis-le.
- Tu donnes une analyse, pas un ordre. Toute suggestion d'achat ou de vente se \
termine par un rappel que ce n'est pas un conseil en investissement.

Portefeuille
{portefeuille}"""


class ErreurModele(Exception):
    pass


class Indisponible(Exception):
    """Refus passager du fournisseur : quota par minute (429) ou surcharge (503)."""
    def __init__(self, delai: float, raison: str):
        self.delai = delai
        self.raison = raison


MAX_ATTENTES = 3
ATTENTE_MAX = 60.0


def _delai_quota(r: httpx.Response) -> float:
    """Combien attendre apres un 429. En-tete Retry-After (Groq, OpenRouter...)
    ou delai ecrit dans le corps (Gemini : « retry in 23.4s », "retryDelay": "23s")."""
    try:
        return min(float(r.headers["retry-after"]), ATTENTE_MAX)
    except (KeyError, ValueError):
        pass
    m = re.search(r'retry in ([\d.]+)\s*s|"retryDelay":\s*"([\d.]+)s"', r.text)
    if m:
        return min(float(m.group(1) or m.group(2)) + 1, ATTENTE_MAX)
    return 20.0


def _nettoyer(texte: str) -> str:
    # Les modeles « a raisonnement » (Qwen3, DeepSeek-R1...) laissent parfois
    # leur brouillon entre balises <think>.
    return re.sub(r"<think>.*?</think>", "", texte or "", flags=re.S).strip()


async def _completion(http: httpx.AsyncClient, modele: str, messages: list, outils: list,
                      choix: str = "auto") -> dict:
    corps = {"model": modele, "messages": messages, "temperature": 0.2,
             "tools": outils, "tool_choice": choix}
    entetes = {"Authorization": "Bearer " + config.LLM_API_KEY} if config.LLM_API_KEY else {}

    for essai in range(3):
        try:
            r = await http.post(config.LLM_BASE_URL + "/chat/completions", json=corps, headers=entetes)
        except httpx.HTTPError as e:
            raise ErreurModele("Fournisseur du modele injoignable (%s)." % type(e).__name__) from e
        # Quota par minute ou surcharge : l'attente se compte en dizaines de
        # secondes, c'est l'appelant qui la gere pour prevenir l'utilisateur.
        if r.status_code == 429:
            raise Indisponible(_delai_quota(r), "quota")
        if r.status_code == 503:
            raise Indisponible(10.0, "surcharge")
        # Autre panne passagere : deux essais de plus, rapproches.
        if r.status_code in (500, 502, 504) and essai < 2:
            await asyncio.sleep(2.0 * (essai + 1))
            continue
        if r.status_code >= 400:
            raise ErreurModele("Le modele a repondu %s : %s" % (r.status_code, r.text[:300]))
        try:
            return r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as e:
            raise ErreurModele("Reponse du modele illisible : %s" % r.text[:300]) from e
    raise ErreurModele("Le fournisseur du modele reste en panne, reessaie dans un instant.")


def _resume(valorisation: dict) -> str:
    """Le portefeuille tel que le modele le lit : sans identifiants de base."""
    lignes = [{k: v for k, v in l.items() if k != "id"} for l in valorisation["lignes"]]
    return json.dumps({**valorisation, "lignes": lignes}, ensure_ascii=False, indent=1)


class _Basculer(Exception):
    """Le modele en cours est indisponible et un modele de secours existe."""


async def repondre(question: str, historique: list[dict], valorisation: dict,
                   date: str | None, session: ClientSession):
    """Generateur d'evenements : "outil" (appel en cours), "attente" (quota,
    surcharge ou bascule vers un modele de secours), puis "reponse".

    Les modeles sont essayes dans l'ordre de LLM_MODEL. Si l'un est sature ou
    hors quota, la question repart de zero avec le suivant : repartir de zero
    evite de melanger l'historique d'appels (et les signatures Gemini) de deux
    modeles differents.
    """
    outils = await marche.outils_pour_modele(session)
    noms = {o["function"]["name"] for o in outils}
    systeme = SYSTEME.format(date=date or "(date inconnue)", portefeuille=_resume(valorisation))

    async def executer(nom: str, arguments: dict | None, brut) -> str:
        if nom not in noms:
            return "Outil inconnu : %s. Outils disponibles : %s." % (nom, ", ".join(sorted(noms)))
        if arguments is None:
            return "Arguments invalides (JSON attendu) : %s" % str(brut)[:200]
        _, texte = await marche.appeler(session, nom, arguments)
        return texte

    async with httpx.AsyncClient(timeout=DELAI) as http:
        for rang, modele in enumerate(config.LLM_MODELS):
            secours = rang + 1 < len(config.LLM_MODELS)
            try:
                async for ev in _tentative(http, modele, secours, systeme, historique, question,
                                           outils, executer):
                    yield ev
                return
            except _Basculer:
                yield {"type": "attente", "raison": "bascule", "secondes": 0,
                       "modele": config.LLM_MODELS[rang + 1]}


async def _tentative(http, modele, secours, systeme, historique, question, outils, executer):
    messages = [{"role": "system", "content": systeme}, *historique, {"role": "user", "content": question}]

    async def appel_modele(choix: str):
        """Sur quota ou surcharge : bascule s'il reste un modele de secours,
        sinon attend en prevenant la page."""
        for _ in range(MAX_ATTENTES):
            try:
                yield {"type": "_message", "message": await _completion(http, modele, messages, outils, choix)}
                return
            except Indisponible as i:
                if secours:
                    raise _Basculer() from i
                yield {"type": "attente", "raison": i.raison, "secondes": round(i.delai)}
                await asyncio.sleep(i.delai)
        raise ErreurModele("Le modele reste indisponible (quota ou surcharge). "
                           "Reessaie dans une minute.")

    for tour in range(MAX_TOURS + 1):
        dernier = tour == MAX_TOURS
        if dernier:
            # Trop d'allers-retours : reponse avec ce qui a deja ete obtenu.
            messages.append({"role": "user", "content":
                             "Reponds maintenant avec les donnees deja obtenues, sans appeler d'autre outil."})
        msg = None
        async for ev in appel_modele("none" if dernier else "auto"):
            if ev["type"] == "_message":
                msg = ev["message"]
            else:
                yield ev

        appels = msg.get("tool_calls") or []
        if dernier or not appels:
            yield {"type": "reponse", "texte": _nettoyer(msg.get("content"))}
            return

        # Message renvoye tel quel, extra_content compris : Gemini 3 y place une
        # signature qu'il exige au tour suivant.
        messages.append({"role": "assistant",
                         **{k: msg[k] for k in ("content", "tool_calls", "extra_content") if k in msg}})

        prepares = []
        for i, appel in enumerate(appels):
            fonction = appel.get("function") or {}
            nom = fonction.get("name", "")
            brut = fonction.get("arguments") or "{}"
            try:
                arguments = json.loads(brut) if isinstance(brut, str) else brut
                if not isinstance(arguments, dict):
                    raise ValueError
            except ValueError:
                arguments = None
            prepares.append((appel.get("id") or "appel_%d_%d" % (tour, i), nom, arguments, brut))
            yield {"type": "outil", "nom": nom, "arguments": arguments}

        # Les outils demandes dans un meme tour partent en parallele.
        resultats = await asyncio.gather(*[executer(n, a, b) for _, n, a, b in prepares])
        for (ident, _, _, _), texte in zip(prepares, resultats):
            messages.append({"role": "tool", "tool_call_id": ident, "content": texte[:MAX_RESULTAT]})
