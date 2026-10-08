import asyncio
import csv
import html
import io
import logging
import os
import re
import sys
from datetime import datetime, time as dtime, timezone
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

# Reglages du mode HTTP (ignores en stdio).
# host et port passent par le constructeur : FastMCP decide de la protection
# DNS-rebinding a la construction. Construit sur 127.0.0.1, il refuserait
# ensuite les requetes adressees a onrender.com.
# stateless_http : Render endort le service gratuit apres 15 min d'inactivite,
# une session gardee en memoire serait perdue au reveil.
mcp = FastMCP(
    "BRVM",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
    stateless_http=True,
)

# Source des donnees. Le depot Fredysessie/brvm-data-public a ete supprime en
# septembre 2026 : les CSV sont desormais produits et heberges par nous.
# Generateur : https://github.com/yanick002/GOMYCODE (brvm-data/scraper/collecte.py)
BASE_URL = "https://raw.githubusercontent.com/yanick002/GOMYCODE/main/brvm-data/data"

STOCK_TICKERS = [
    "ABJC", "BBGC", "BICB", "BICC", "BNBC", "BOAB", "BOABF", "BOAC", "BOAM", "BOAN",
    "BOAS", "CABC", "CBIBF", "CFAC", "CIEC", "ECOC", "ETIT", "FTSC", "LNBB", "NEIC",
    "NSBC", "NTLC", "ONTBF", "ORAC", "ORGT", "PALC", "PRSC", "SAFC", "SCRC", "SDCC",
    "SDSC", "SEMC", "SGBC", "SHEC", "SIBC", "SICC", "SIVC", "SLBC", "SMBC", "SNTS",
    "SOGC", "SPHC", "STAC", "STBC", "TTLC", "TTLS", "UNLC", "UNXC",
]
# BBGC (Bridge Bank Group CI) ajoute : premiere cotation le 24/09/2026.
# SVOC retire : plus aucune cotation depuis le 10/05/2019.

INDEX_TICKERS = [
    "BRVM-CB", "BRVM-CD", "BRVM-EN", "BRVM-IN", "BRVM-SF", "BRVM-SP", "BRVM-TEL",
    "BRVM30", "BRVMAG", "BRVMAS", "BRVMC", "BRVMDI", "BRVMFI", "BRVMIN",
    "BRVMPA", "BRVMPR", "BRVMSP", "BRVMTR",
]

ALL_TICKERS = STOCK_TICKERS + INDEX_TICKERS

# Cours en seance. Les CSV ne contiennent que les clotures (une ligne par seance, publiee apres 15h00) : pendant la
# seance, le dernier cours de chaque action est lu sur la page « toutes les actions » de Sika Finance (une requete
# pour les 48 titres) et garde 15 minutes en memoire. Quel que soit le nombre d'appels, Sika recoit donc au plus une
# requete par quart d'heure. Hors seance, et si Sika ne repond pas, tout reste sur la derniere cloture.
# L'historique et les indicateurs (RSI, Beta, variations sur 1 mois, 1 an...) restent calcules sur les clotures.
SIKA_URL = "https://www.sikafinance.com/marches/aaz"
MEMOIRE_DIRECT = 15 * 60          # secondes
REESSAI_DIRECT = 2 * 60           # apres un echec, on ne redemande pas avant 2 minutes
_direct = {"releve": None, "titres": {}, "echec": None}
_verrou_direct = asyncio.Lock()
journal = logging.getLogger("brvm.direct")


def seance_ouverte(maintenant: Optional[datetime] = None) -> bool:
    """Du lundi au vendredi, de 9h00 a 15h00 UTC (heure d'Abidjan) : les heures de la BRVM."""
    m = maintenant or datetime.now(timezone.utc)
    return m.weekday() < 5 and dtime(9, 0) <= m.time() < dtime(15, 0)


def _texte(cellule: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cellule))).replace("\xa0", " ").strip()


def _montant(texte: str) -> Optional[float]:
    """« 26 581 555 », « 3 055 », « -2.78% » -> nombre ; None si vide ou illisible."""
    t = texte.replace("%", "").replace(" ", "").replace(",", ".").strip()
    try:
        return float(t)
    except ValueError:
        return None


def lire_page_sika(page: str) -> dict:
    """{ticker: {ouverture, plus_haut, plus_bas, volume_titres, volume_xof, dernier, variation}} depuis la page Sika.

    Le tableau voulu est celui dont l'en-tete annonce « Volume (titres) » ; le symbole vient du lien de chaque ligne
    (/marches/cotation_SNTS.sn). Colonnes : Nom, Ouverture, +Haut, +Bas, Volume (titres), Volume (XOF), Dernier, Variation.
    """
    for tableau in re.findall(r"<table.*?</table>", page, re.S):
        entetes = [_texte(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", tableau, re.S)]
        if "Volume (titres)" not in entetes or "Dernier" not in entetes:
            continue
        titres = {}
        for ligne in re.findall(r"<tr[^>]*>(.*?)</tr>", tableau, re.S):
            code = re.search(r"cotation_([A-Z0-9]+)\.", ligne)
            cellules = [_texte(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", ligne, re.S)]
            if not code or len(cellules) < 8:
                continue
            ouverture, haut, bas, vol_titres, vol_xof, dernier, variation = (_montant(c) for c in cellules[1:8])
            if dernier is None or dernier <= 0:
                continue
            titres[code.group(1)] = {"ouverture": ouverture, "plus_haut": haut, "plus_bas": bas,
                                     "volume_titres": vol_titres, "volume_xof": vol_xof,
                                     "dernier": dernier, "variation": variation}
        return titres
    return {}


async def cours_direct() -> Optional[dict]:
    """{"releve": heure du releve (UTC, ISO), "titres": {...}} pendant la seance ; None hors seance ou si Sika ne
    repond pas (on garde alors la cloture)."""
    if not seance_ouverte():
        return None
    async with _verrou_direct:
        maintenant = datetime.now(timezone.utc)
        releve, echec = _direct["releve"], _direct["echec"]
        frais = releve is not None and releve.date() == maintenant.date() and (maintenant - releve).total_seconds() < MEMOIRE_DIRECT
        recent_echec = echec is not None and (maintenant - echec).total_seconds() < REESSAI_DIRECT
        if not frais and not recent_echec:
            try:
                async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (Mansa)", "Accept-Language": "fr"},
                                             follow_redirects=True) as client:
                    r = await client.get(SIKA_URL, timeout=20)
                    r.raise_for_status()
                titres = lire_page_sika(r.text)
                # moins de la moitie des actions lues : la page a change, on ne s'y fie pas
                if len(titres) < len(STOCK_TICKERS) / 2:
                    raise ValueError("%d titre(s) lu(s) sur la page Sika" % len(titres))
                _direct.update(releve=maintenant, titres=titres, echec=None)
            except Exception as e:
                journal.warning("Sika Finance : cours en seance indisponibles (%s), on garde la cloture", e)
                _direct["echec"] = maintenant
        releve = _direct["releve"]
        if releve is None or releve.date() != maintenant.date():
            return None             # rien de releve aujourd'hui : on reste sur la cloture
        return {"releve": releve.strftime("%Y-%m-%dT%H:%M:%SZ"), "titres": _direct["titres"]}


def _variation_texte(v: float) -> str:
    """1.33 -> « +1,33% », comme dans les CSV."""
    return ("%+.2f%%" % v).replace(".", ",")


def avec_direct(ligne: dict, direct: Optional[dict]) -> dict:
    """Une ligne d'indicateurs (CSV) dont le cours du jour est remplace par le cours en seance, s'il y en a un.

    Cours_Source vaut « seance » ou « cloture » ; Cours_Releve donne l'heure du releve Sika (UTC). La cloture de la
    veille devient la derniere cloture connue, puisque la seance du jour n'est pas encore dans les CSV.
    """
    ligne = dict(ligne)
    d = (direct or {}).get("titres", {}).get(ligne.get("Ticker"))
    if not d:
        ligne.update(Cours_Source="cloture", Cours_Releve="")
        return ligne
    ligne.update(
        Cloture_Veille=ligne.get("Cours_Actuel"),
        Cours_Actuel=d["dernier"],
        Variation_Cours=_variation_texte(d["variation"]) if d["variation"] is not None else ligne.get("Variation_Cours"),
        Ouverture=d["ouverture"] if d["ouverture"] is not None else "",
        Plus_Haut=d["plus_haut"] if d["plus_haut"] is not None else "",
        Plus_Bas=d["plus_bas"] if d["plus_bas"] is not None else "",
        Volume_Titres=d["volume_titres"] if d["volume_titres"] is not None else "",
        Volume_XOF=d["volume_xof"] if d["volume_xof"] is not None else "",
        Cours_Source="seance",
        Cours_Releve=direct["releve"],
    )
    return ligne


def _parse_float(val) -> float:
    try:
        return float(str(val).replace("%", "").replace(",", ".").strip())
    except Exception:
        return 0.0


def _parse_optional(val):
    """Comme _parse_float, mais rend None sur un champ vide au lieu de 0.0.

    La difference compte : un titre sans RSI n'est pas un titre a RSI zero.
    BBGC, cotee depuis deux seances, n'a pas encore de RSI ; lue comme 0.0
    elle sortait en tete des titres les plus survendus du marche.
    """
    if val is None:
        return None
    texte = str(val).replace("%", "").replace(",", ".").strip()
    if not texte:
        return None
    try:
        return float(texte)
    except Exception:
        return None


@mcp.tool()
def get_all_tickers() -> dict:
    """Retourne la liste de tous les tickers disponibles à la BRVM (actions et indices)."""
    return {"stocks": STOCK_TICKERS, "indices": INDEX_TICKERS}


@mcp.tool()
async def get_ticker_data(ticker: str, period: str = "daily", limit: int = 60) -> str:
    """
    Récupère l'historique de prix OHLCV d'un ticker.

    Args:
        ticker: Symbole boursier (ex: SGBC, ONTBF, BOABF)
        period: Période — daily, weekly, monthly, quarterly, yearly
        limit: Nombre de lignes les plus récentes à retourner (défaut 60)
    """
    url = f"{BASE_URL}/{ticker}/{ticker}.{period}.csv"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=15)
        r.raise_for_status()
    lines = r.text.strip().split("\n")
    header = lines[0]
    recent = lines[max(1, len(lines) - limit):]
    return "\n".join([header] + recent)


@mcp.tool()
async def get_indicators(ticker: str) -> dict:
    """
    Récupère les indicateurs techniques actuels d'un ticker :
    RSI, Beta, cours actuel, variation du jour, volumes, plages de prix
    sur 1 semaine / 1 mois / YTD / 1 an / 3 ans / 5 ans.
    Pendant la séance (lundi-vendredi, 9h00-15h00 UTC), Cours_Actuel, Variation_Cours, Ouverture, Plus_Haut,
    Plus_Bas et les volumes sont ceux de la séance en cours (Cours_Source = "seance", heure du relevé dans
    Cours_Releve, rafraîchi toutes les 15 minutes) ; sinon ceux de la dernière clôture (Cours_Source = "cloture").
    RSI, Beta et variations sur plusieurs jours restent calculés sur les clôtures.

    Args:
        ticker: Symbole boursier (ex: SGBC, ONTBF)
    """
    url = f"{BASE_URL}/{ticker}/{ticker}.indicator.csv"

    async def lire_csv():
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
        return dict(next(csv.DictReader(io.StringIO(r.text))))

    ligne, direct = await asyncio.gather(lire_csv(), cours_direct())
    return avec_direct(ligne, direct)


@mcp.tool()
async def screen_market(
    rsi_max: Optional[float] = None,
    rsi_min: Optional[float] = None,
    variation_1m_min: Optional[float] = None,
    variation_1m_max: Optional[float] = None,
    variation_1y_min: Optional[float] = None,
    variation_1y_max: Optional[float] = None,
    stocks_only: bool = True,
) -> list:
    """
    Scanne le marché BRVM et filtre les titres selon des critères.
    Les variations sont en décimales (ex: -0.10 = -10%, 0.05 = +5%).

    Args:
        rsi_max: RSI maximum (ex: 35 pour trouver les titres survendus)
        rsi_min: RSI minimum (ex: 70 pour trouver les titres surachetés)
        variation_1m_min: Variation 1 mois minimum (ex: 0.05 = +5%)
        variation_1m_max: Variation 1 mois maximum (ex: -0.05 = -5%)
        variation_1y_min: Variation 1 an minimum
        variation_1y_max: Variation 1 an maximum
        stocks_only: Si True, exclut les indices (défaut True)

    Pendant la séance (lundi-vendredi, 9h00-15h00 UTC), cours, variation du jour, plus haut et plus bas sont ceux
    de la séance en cours (source_cours = "seance", heure du relevé dans releve).
    """
    tickers = STOCK_TICKERS if stocks_only else ALL_TICKERS

    async def fetch_one(client: httpx.AsyncClient, ticker: str):
        try:
            url = f"{BASE_URL}/{ticker}/{ticker}.indicator.csv"
            r = await client.get(url, timeout=15)
            if r.status_code != 200:
                return None
            row = dict(next(csv.DictReader(io.StringIO(r.text))))
            return ticker, row
        except Exception:
            return None

    async with httpx.AsyncClient() as client:
        results_raw, direct = await asyncio.gather(
            asyncio.gather(*[fetch_one(client, t) for t in tickers]), cours_direct())

    # Sans ce garde-fou, une source injoignable renvoyait une liste vide, qui se
    # lit comme "aucun titre ne correspond aux criteres". C'est ce qui a masque
    # la panne de septembre 2026 pendant une semaine.
    recuperes = sum(1 for x in results_raw if x is not None)
    if recuperes < len(tickers) / 2:
        raise RuntimeError(
            "Donnees indisponibles : %d ticker(s) sur %d seulement ont repondu. "
            "Le screener ne renvoie PAS un resultat vide, il signale la panne. "
            "Verifier %s" % (recuperes, len(tickers), BASE_URL)
        )

    results = []
    for item in results_raw:
        if item is None:
            continue
        ticker, row = item
        row = avec_direct(row, direct)
        rsi = _parse_optional(row.get("RSI"))
        var_1m = _parse_optional(row.get("1_Mois_Variation"))
        var_1y = _parse_optional(row.get("1_An_Variation"))

        # Un titre dont la valeur filtree est inconnue est ecarte du resultat,
        # jamais assimile a zero. Une nouvelle cotation n'a ni RSI ni recul sur
        # un an : la faire passer pour survendue serait un faux signal d'achat.
        if (rsi_max is not None or rsi_min is not None) and rsi is None:
            continue
        if (variation_1m_min is not None or variation_1m_max is not None) and var_1m is None:
            continue
        if (variation_1y_min is not None or variation_1y_max is not None) and var_1y is None:
            continue

        if rsi_max is not None and rsi > rsi_max:
            continue
        if rsi_min is not None and rsi < rsi_min:
            continue
        if variation_1m_min is not None and var_1m < variation_1m_min:
            continue
        if variation_1m_max is not None and var_1m > variation_1m_max:
            continue
        if variation_1y_min is not None and var_1y < variation_1y_min:
            continue
        if variation_1y_max is not None and var_1y > variation_1y_max:
            continue

        results.append({
            "ticker": ticker,
            "cours": row.get("Cours_Actuel"),
            "variation_jour": row.get("Variation_Cours"),
            "rsi": round(rsi, 2) if rsi is not None else None,
            "beta_1an": row.get("Beta_1_An") or None,
            "variation_1m": f"{var_1m:.2%}" if var_1m is not None else None,
            "variation_1y": f"{var_1y:.2%}" if var_1y is not None else None,
            "volume_xof": row.get("Volume_XOF"),
            "valorisation": row.get("Valorisation"),
            "plus_haut": row.get("Plus_Haut") or None,
            "plus_bas": row.get("Plus_Bas") or None,
            "source_cours": row["Cours_Source"],
            "releve": row["Cours_Releve"] or None,
        })

    # Les titres sans RSI ferment la liste au lieu de l'ouvrir.
    results.sort(key=lambda x: (x["rsi"] is None, x["rsi"] or 0))
    return results


@mcp.tool()
async def get_market_overview() -> dict:
    """
    Donne un aperçu rapide du marché BRVM :
    nombre de titres en hausse/baisse/stable, RSI moyen, top 5 hausses et baisses du jour.
    Pendant la séance (lundi-vendredi, 9h00-15h00 UTC), cours et variations sont ceux de la séance en cours.
    """
    async def fetch_one(client: httpx.AsyncClient, ticker: str):
        try:
            url = f"{BASE_URL}/{ticker}/{ticker}.indicator.csv"
            r = await client.get(url, timeout=15)
            if r.status_code != 200:
                return None
            return dict(next(csv.DictReader(io.StringIO(r.text))))
        except Exception:
            return None

    async with httpx.AsyncClient() as client:
        rows_raw, direct = await asyncio.gather(
            asyncio.gather(*[fetch_one(client, t) for t in STOCK_TICKERS]), cours_direct())

    rows = [avec_direct(r, direct) for r in rows_raw if r is not None]

    # Meme garde-fou que screen_market : echouer bruyamment plutot que rendre
    # un marche a zero hausse et zero baisse, qui ressemble a une seance calme.
    if len(rows) < len(STOCK_TICKERS) / 2:
        raise RuntimeError(
            "Donnees indisponibles : %d ticker(s) sur %d seulement ont repondu. "
            "L'apercu du marche ne renvoie PAS des compteurs a zero, il signale "
            "la panne. Verifier %s" % (len(rows), len(STOCK_TICKERS), BASE_URL)
        )

    hausse, baisse, stable = [], [], []
    rsi_values = []

    for row in rows:
        var_str = str(row.get("Variation_Cours", "0%")).replace(",", ".").replace("%", "").strip()
        try:
            var = float(var_str)
        except Exception:
            var = 0.0
        entry = {"ticker": row.get("Ticker"), "variation_jour": row.get("Variation_Cours"), "cours": row.get("Cours_Actuel")}
        if var > 0:
            hausse.append(entry)
        elif var < 0:
            baisse.append(entry)
        else:
            stable.append(entry)
        rsi = _parse_float(row.get("RSI", 0))
        if rsi > 0:
            rsi_values.append(rsi)

    hausse.sort(key=lambda x: _parse_float(str(x["variation_jour"]).replace(",", ".")), reverse=True)
    baisse.sort(key=lambda x: _parse_float(str(x["variation_jour"]).replace(",", ".")))

    return {
        "titres_en_hausse": len(hausse),
        "titres_en_baisse": len(baisse),
        "titres_stables": len(stable),
        "rsi_moyen_marche": round(sum(rsi_values) / len(rsi_values), 2) if rsi_values else None,
        "top5_hausses": hausse[:5],
        "top5_baisses": baisse[:5],
        # pendant la seance : heure du releve des cours (UTC) ; None = cours de la derniere cloture
        "cours_en_seance_releves_a": direct["releve"] if direct else None,
    }


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Sonde de Render. Ne lit pas les donnees : repond meme si GitHub est lent."""
    return JSONResponse({"status": "ok"})


def main():
    """Point d'entree.

    python server.py         stdio, lance par Claude Code via ~/.claude.json
    python server.py --http  Streamable HTTP sur 0.0.0.0:$PORT/mcp (Render)
    """
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
