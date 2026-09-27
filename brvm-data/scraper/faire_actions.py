# -*- coding: utf-8 -*-
"""Genere scraper/actions.json : le nombre de titres de chaque societe.

Source : la fiche societe de richbourse, qui publie « Nombre de titres ».
Ce n'est PAS derivable des cours : il faut ce chiffre pour calculer la
valorisation et le capital echange.

Script ponctuel, pas appele par le cron. A relancer quand une societe
augmente son capital ou fractionne son titre (Sonatel, 26 octobre 2026).

Usage :
    python faire_actions.py
"""
import json, os, re, sys, time, urllib.error, urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ICI = os.path.dirname(os.path.abspath(__file__))
TICKERS = os.path.join(ICI, "tickers.json")
SORTIE = os.path.join(ICI, "actions.json")

FICHE = "https://www.richbourse.com/common/apprendre/details-societe/%s"
ENTETES = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
           "Accept": "text/html,application/xhtml+xml"}
MOTIF = re.compile(r"Nombre de titres\s*:?\s*</?[^>]*>?\s*([0-9][0-9\s  .,]*)")


def lire(ticker):
    req = urllib.request.Request(FICHE % ticker, headers=ENTETES)
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    texte = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", html, flags=re.S))
    m = re.search(r"Nombre de titres\s*:?\s*([0-9][0-9\s  ]*)", texte)
    if not m:
        raise ValueError("champ 'Nombre de titres' absent de la fiche")
    n = int(re.sub(r"[^0-9]", "", m.group(1)))
    if n <= 0:
        raise ValueError("nombre de titres nul")
    return n


def main():
    tickers = sorted(json.load(open(TICKERS, encoding="utf-8"))["actions"])
    ancien = {}
    if os.path.exists(SORTIE):
        ancien = json.load(open(SORTIE, encoding="utf-8")).get("actions", {})

    resultat, echecs = {}, []
    for i, t in enumerate(tickers, 1):
        try:
            n = lire(t)
            avant = ancien.get(t)
            marque = ""
            if avant and avant != n:
                marque = "  <-- CHANGEMENT, etait %s" % format(avant, ",").replace(",", " ")
            print("  [%2d/%2d] %-6s %15s%s" % (i, len(tickers), t,
                                               format(n, ",").replace(",", " "), marque))
            resultat[t] = n
        except Exception as e:
            print("  [%2d/%2d] %-6s ECHEC : %s" % (i, len(tickers), t, e))
            echecs.append(t)
            if t in ancien:                 # ne jamais perdre une valeur connue
                resultat[t] = ancien[t]
        time.sleep(0.5)

    json.dump({
        "_source": "richbourse.com/common/apprendre/details-societe/{TICKER}, champ "
                   "'Nombre de titres'. Regenere par faire_actions.py.",
        "_attention": "Sonatel (SNTS) passe de 100 000 000 a 1 000 000 000 titres le "
                      "26 octobre 2026 (fractionnement 1 pour 10). Relancer ce script "
                      "ce jour-la.",
        "actions": dict(sorted(resultat.items())),
    }, open(SORTIE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(SORTIE, "a", encoding="utf-8").write("\n")

    print("\n%d/%d releves, ecrit dans %s" % (len(tickers) - len(echecs), len(tickers), SORTIE))
    if echecs:
        print("Echecs (valeur precedente conservee) : %s" % ", ".join(echecs))

    # Si la fiche societe change de forme, tous les releves echouent d'un coup.
    # Sans ce garde-fou le job passerait au vert en recopiant l'ancien fichier.
    if len(echecs) > len(tickers) / 2:
        print("ALERTE : la fiche societe de richbourse n'est plus lisible.")
        sys.exit(1)


if __name__ == "__main__":
    main()
