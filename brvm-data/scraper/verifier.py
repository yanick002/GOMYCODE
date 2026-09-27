# -*- coding: utf-8 -*-
"""Controle de coherence des fichiers produits par collecte.py.

Verifie que chaque agregation (weekly, monthly, quarterly, yearly) retombe
sur la meme cloture finale et le meme volume cumule que le daily dont elle
est tiree. Une agregation qui derive passe inapercue autrement : les cinq
fichiers ont l'air corrects pris separement.

Usage :
    python verifier.py              NSBC, SNTS, BRVMC
    python verifier.py NSBC BICC    ceux-la
    python verifier.py --tout       les 67 tickers
"""
import csv, json, os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ICI = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(ICI), "data")
PERIODES = ["weekly", "monthly", "quarterly", "yearly"]


def verifier(ticker):
    """Retourne le nombre d'anomalies trouvees."""
    dossier = os.path.join(DATA, ticker)
    quotidien = os.path.join(dossier, "%s.daily.csv" % ticker)
    if not os.path.exists(quotidien):
        print("%-8s daily.csv absent" % ticker)
        return 1

    base = list(csv.DictReader(open(quotidien, encoding="utf-8")))
    if not base:
        print("%-8s daily.csv vide" % ticker)
        return 1

    cloture = base[-1]["Close"]
    volume = sum(float(r["Volume"] or 0) for r in base)
    print("%-8s %5d seances, du %s au %s"
          % (ticker, len(base), base[0]["Date"], base[-1]["Date"]))

    anomalies = 0
    for periode in PERIODES:
        chemin = os.path.join(dossier, "%s.%s.csv" % (ticker, periode))
        if not os.path.exists(chemin):
            print("           %-10s ABSENT" % periode)
            anomalies += 1
            continue
        lignes = list(csv.DictReader(open(chemin, encoding="utf-8")))
        if not lignes:
            print("           %-10s VIDE" % periode)
            anomalies += 1
            continue
        v = sum(float(r["Volume"] or 0) for r in lignes)
        ecart_close = lignes[-1]["Close"] != cloture
        ecart_vol = abs(v - volume) >= 1
        anomalies += ecart_close + ecart_vol
        print("           %-10s %4d lignes  cloture %s  volume %s"
              % (periode, len(lignes),
                 "DIFFERENTE" if ecart_close else "OK",
                 "DIFFERENT" if ecart_vol else "OK"))
    return anomalies


def main():
    if "--tout" in sys.argv:
        ici = os.path.dirname(os.path.abspath(__file__))
        cibles = sorted(json.load(open(os.path.join(ici, "tickers.json"), encoding="utf-8"))["actions"])
        cibles += sorted(json.load(open(os.path.join(ici, "indices.json"), encoding="utf-8"))["indices"])
    else:
        cibles = [t.upper() for t in sys.argv[1:] if not t.startswith("--")] \
                 or ["NSBC", "SNTS", "BRVMC"]

    total = sum(verifier(t) for t in cibles)
    print()
    if total:
        print("%d anomalie(s) sur %d ticker(s)." % (total, len(cibles)))
        sys.exit(1)
    print("%d ticker(s) verifie(s), agregations coherentes." % len(cibles))


if __name__ == "__main__":
    main()
