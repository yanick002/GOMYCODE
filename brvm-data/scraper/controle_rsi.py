# -*- coding: utf-8 -*-
"""Compare notre RSI calcule a celui que richbourse affiche sur sa page
technique (attribut title d'un bloc rb-visible-stat).

C'est un CONTROLE, pas une source : richbourse n'est jamais recopie dans nos
CSV. Le but est de detecter une derive de methode (moyenne simple au lieu du
lissage de Wilder, mauvaise serie de clotures, decalage d'un jour).

Usage :
    python controle_rsi.py            un echantillon de 12 tickers
    python controle_rsi.py --tout     les 49 actions
    python controle_rsi.py NSBC SNTS  ceux-la
"""
import csv, json, os, re, sys, time, urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ICI = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(ICI), "data")
PAGE = "https://www.richbourse.com/common/mouvements/technique/%s"
ENTETES = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/html"}
MOTIF = re.compile(r"Le RSI 14 jours est de ([0-9]+[,.][0-9]+)\s*%")
ECART_MAX = 0.5          # au-dela, la methode diverge


def rsi_richbourse(ticker):
    req = urllib.request.Request(PAGE % ticker, headers=ENTETES)
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    m = MOTIF.search(html)
    if not m:
        raise ValueError("RSI absent de la page")
    return float(m.group(1).replace(",", "."))


def rsi_local(ticker):
    chemin = os.path.join(DATA, ticker, "%s.indicator.csv" % ticker)
    if not os.path.exists(chemin):
        raise IOError("indicator.csv absent")
    valeur = next(csv.DictReader(open(chemin, encoding="utf-8")))["RSI"]
    if not valeur:
        raise ValueError("RSI vide (historique trop court)")
    return float(valeur)


def main():
    tous = sorted(json.load(open(os.path.join(ICI, "tickers.json"), encoding="utf-8"))["actions"])
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        cibles = [a.upper() for a in args]
    elif "--tout" in sys.argv:
        cibles = tous
    else:
        cibles = tous[::max(1, len(tous) // 12)][:12]

    print("%-8s %10s %12s %10s" % ("Ticker", "calcule", "richbourse", "ecart"))
    print("-" * 44)
    ecarts, erreurs = [], []
    for t in cibles:
        try:
            a, b = rsi_local(t), rsi_richbourse(t)
        except Exception as e:
            print("%-8s %s" % (t, e))
            erreurs.append(t)
            time.sleep(0.4)
            continue
        d = abs(a - b)
        ecarts.append((t, d))
        print("%-8s %10.2f %12.2f %10.2f %s"
              % (t, a, b, d, "" if d <= ECART_MAX else "  <-- DIVERGENCE"))
        time.sleep(0.4)

    if not ecarts:
        print("\nAucune comparaison possible.")
        sys.exit(1)
    pire = max(ecarts, key=lambda x: x[1])
    moyen = sum(d for _, d in ecarts) / len(ecarts)
    print("\n%d compare(s) — ecart moyen %.3f, pire %s a %.2f"
          % (len(ecarts), moyen, pire[0], pire[1]))
    if erreurs:
        print("Non compares : %s" % ", ".join(erreurs))
    if pire[1] > ECART_MAX:
        print("\nLe calcul du RSI diverge de la reference. Verifier indicateurs.py.")
        sys.exit(1)
    print("Methode conforme.")


if __name__ == "__main__":
    main()
