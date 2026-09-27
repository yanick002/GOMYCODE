# -*- coding: utf-8 -*-
"""Phase 2 — calcule data/{T}/{T}.indicator.csv a partir des cours collectes.

Le serveur MCP ne calcule rien : il recopie ce fichier. Les 32 colonnes
ci-dessous sont donc la seule source de RSI, de Beta et de variations pour
get_indicators, screen_market et get_market_overview.

Ne lit aucune donnee du reseau : tout vient de data/*/*.daily.csv, produit
par collecte.py. Lancer collecte.py AVANT.

Usage :
    python indicateurs.py           tous les tickers
    python indicateurs.py NSBC      seulement celui-la
"""
import csv, datetime, io, json, math, os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER_DATA = os.path.join(RACINE, "data")
ICI = os.path.dirname(os.path.abspath(__file__))

TICKER_INDICE = "BRVMC"      # reference du Beta : le BRVM Composite

# Fenetres, en nombre de seances.
FENETRES = [
    ("1_Semaine", 5),
    ("1_Mois", 21),
    ("1_An", 252),
    ("3_Ans", 756),
    ("5_Ans", 1260),
]

COLONNES = [
    "Ticker", "URL_Ticker", "Cours_Actuel", "Variation_Cours", "Volume_Titres",
    "Volume_XOF", "Ouverture", "Plus_Haut", "Plus_Bas", "Cloture_Veille",
    "Beta_1_An", "RSI", "Capital_Echange", "Valorisation",
    "1_Semaine_Plus_Haut", "1_Semaine_Plus_Bas", "1_Semaine_Variation",
    "1_Mois_Plus_Haut", "1_Mois_Plus_Bas", "1_Mois_Variation",
    "1er_Janvier_Plus_Haut", "1er_Janvier_Plus_Bas", "1er_Janvier_Variation",
    "1_An_Plus_Haut", "1_An_Plus_Bas", "1_An_Variation",
    "3_Ans_Plus_Haut", "3_Ans_Plus_Bas", "3_Ans_Variation",
    "5_Ans_Plus_Haut", "5_Ans_Plus_Bas", "5_Ans_Variation",
]


# ------------------------------------------------------------------ lecture

def lire_daily(ticker):
    """Retourne [(date, open, high, low, close, volume), ...] trie par date."""
    chemin = os.path.join(DOSSIER_DATA, ticker, "%s.daily.csv" % ticker)
    if not os.path.exists(chemin):
        raise IOError("%s.daily.csv absent — lancer collecte.py d'abord" % ticker)

    lignes = []
    with open(chemin, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                jour = datetime.date.fromisoformat(r["Date"])
                cloture = float(r["Close"])
            except (ValueError, KeyError, TypeError):
                continue                      # ligne illisible : on l'ignore
            if cloture <= 0:
                continue

            def opt(nom, defaut):
                try:
                    return float(r[nom])
                except (ValueError, KeyError, TypeError):
                    return defaut

            lignes.append((jour, opt("Open", cloture), opt("High", cloture),
                           opt("Low", cloture), cloture, opt("Volume", 0.0)))
    lignes.sort(key=lambda x: x[0])
    return lignes


# ------------------------------------------------------------------ calculs

def rsi_wilder(clotures, periode=14):
    """RSI de Wilder. Une moyenne mobile simple donnerait un autre chiffre,
    qui ne correspondrait a aucun RSI publie ailleurs."""
    if len(clotures) < periode + 1:
        return None

    gains, pertes = [], []
    for i in range(1, len(clotures)):
        ecart = clotures[i] - clotures[i - 1]
        gains.append(max(ecart, 0.0))
        pertes.append(max(-ecart, 0.0))

    moy_gain = sum(gains[:periode]) / periode
    moy_perte = sum(pertes[:periode]) / periode
    for i in range(periode, len(gains)):
        moy_gain = (moy_gain * (periode - 1) + gains[i]) / periode
        moy_perte = (moy_perte * (periode - 1) + pertes[i]) / periode

    if moy_perte == 0:
        return 100.0 if moy_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + moy_gain / moy_perte)


def beta_1_an(par_date, indice_par_date, seances=252, tolerance=10):
    """Covariance des rendements quotidiens divisee par la variance de ceux de
    l'indice, sur les dernieres seances communes.

    Rend None si le titre ne cote plus : un « Beta 1 an » calcule sur 2019
    pour un titre radie serait un chiffre faux presente comme frais.
    """
    communes = sorted(set(par_date) & set(indice_par_date))
    if len(communes) < 30:
        return None

    dernieres_indice = sorted(indice_par_date)[-tolerance:]
    if communes[-1] < dernieres_indice[0]:
        return None                        # titre decroche de la cote

    communes = communes[-(seances + 1):]
    rt, ri = [], []
    for a, b in zip(communes, communes[1:]):
        p0, p1 = par_date[a], par_date[b]
        q0, q1 = indice_par_date[a], indice_par_date[b]
        if p0 <= 0 or q0 <= 0:
            continue
        rt.append(p1 / p0 - 1.0)
        ri.append(q1 / q0 - 1.0)

    n = len(rt)
    if n < 30:
        return None
    mt, mi = sum(rt) / n, sum(ri) / n
    variance = sum((x - mi) ** 2 for x in ri)
    if variance == 0:
        return None
    covariance = sum((rt[i] - mt) * (ri[i] - mi) for i in range(n))
    return covariance / variance


def sur_fenetre(lignes):
    """(plus_haut, plus_bas, variation) sur les lignes fournies.

    Plus haut et plus bas viennent des colonnes High et Low, pas des clotures :
    une meche ne se voit pas en cloture. La variation compare la derniere
    cloture a la premiere cloture DE LA FENETRE.
    """
    if not lignes:
        return None, None, None
    haut = max(l[2] for l in lignes)
    bas = min(l[3] for l in lignes)
    premier, dernier = lignes[0][4], lignes[-1][4]
    variation = (dernier / premier - 1.0) if premier > 0 else None
    return haut, bas, variation


# ---------------------------------------------------------------- formatage

def nb(x):
    """Flottant, ou chaine vide si la valeur n'existe pas."""
    return "" if x is None else repr(float(x))


def arrondi(x, n):
    return "" if x is None else repr(round(float(x), n))


def pourcentage_jour(veille, cloture):
    """Chaine « +0,71% » : c'est ce format precis que get_market_overview lit
    pour compter les hausses et les baisses. Virgule decimale, signe explicite
    sauf a zero."""
    if veille is None or veille <= 0:
        return ""
    p = (cloture / veille - 1.0) * 100.0
    if abs(p) < 0.005:
        return "0,00%"
    return ("%+.2f%%" % p).replace(".", ",")


# ------------------------------------------------------------------ une ligne

def calculer(ticker, url_ticker, nb_actions, indice_par_date):
    lignes = lire_daily(ticker)
    if len(lignes) < 2:
        raise ValueError("seulement %d seance(s) dans le daily" % len(lignes))

    jour, ouverture, haut, bas, cloture, volume = lignes[-1]
    veille = lignes[-2][4]
    clotures = [l[4] for l in lignes]

    v = {c: "" for c in COLONNES}
    v["Ticker"] = ticker
    v["URL_Ticker"] = url_ticker
    v["Cours_Actuel"] = nb(cloture)
    v["Variation_Cours"] = pourcentage_jour(veille, cloture)
    v["Volume_Titres"] = nb(volume)
    v["Ouverture"] = nb(ouverture)
    v["Plus_Haut"] = nb(haut)
    v["Plus_Bas"] = nb(bas)
    v["Cloture_Veille"] = nb(veille)
    v["RSI"] = arrondi(rsi_wilder(clotures), 2)

    if nb_actions:
        # Sans le nombre de titres, ces deux champs seraient inventes : on les
        # laisse vides plutot que de publier un chiffre faux.
        v["Volume_XOF"] = nb(volume * cloture)
        v["Valorisation"] = nb(nb_actions * cloture)
        v["Capital_Echange"] = arrondi(volume / nb_actions, 4)

    par_date = {l[0]: l[4] for l in lignes}
    if ticker == TICKER_INDICE:
        v["Beta_1_An"] = "1.0"            # l'indice contre lui-meme
    else:
        v["Beta_1_An"] = arrondi(beta_1_an(par_date, indice_par_date), 2)

    for nom, taille in FENETRES:
        if len(lignes) < taille:
            # Pas assez d'historique : on laisse les trois champs vides.
            # Afficher la variation de BBGC sur deux seances dans la
            # colonne « 1 an » en ferait un faux signal pour le screener.
            continue
        h, b, var = sur_fenetre(lignes[-taille:])
        v["%s_Plus_Haut" % nom] = nb(h)
        v["%s_Plus_Bas" % nom] = nb(b)
        v["%s_Variation" % nom] = arrondi(var, 6)

    annee = jour.year
    h, b, var = sur_fenetre([l for l in lignes if l[0].year == annee])
    v["1er_Janvier_Plus_Haut"] = nb(h)
    v["1er_Janvier_Plus_Bas"] = nb(b)
    v["1er_Janvier_Variation"] = arrondi(var, 6)

    return v, jour


def ecrire(ticker, valeurs):
    """Ecrit l'indicator.csv. Retourne True si le contenu a change."""
    tampon = io.StringIO()
    w = csv.writer(tampon, lineterminator="\n")
    w.writerow(COLONNES)
    w.writerow([valeurs[c] for c in COLONNES])
    contenu = tampon.getvalue()

    chemin = os.path.join(DOSSIER_DATA, ticker, "%s.indicator.csv" % ticker)
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    if os.path.exists(chemin):
        with open(chemin, encoding="utf-8") as f:
            if f.read() == contenu:
                return False
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        f.write(contenu)
    return True


# -------------------------------------------------------------------- main

def main():
    actions = json.load(open(os.path.join(ICI, "tickers.json"), encoding="utf-8"))["actions"]
    indices = json.load(open(os.path.join(ICI, "indices.json"), encoding="utf-8"))["indices"]
    nb_titres = json.load(open(os.path.join(ICI, "actions.json"), encoding="utf-8"))["actions"]

    try:
        indice_par_date = {l[0]: l[4] for l in lire_daily(TICKER_INDICE)}
        print("Reference Beta : %s, %d seances\n" % (TICKER_INDICE, len(indice_par_date)))
    except Exception as e:
        print("ATTENTION : %s indisponible (%s). Beta_1_An restera vide.\n"
              % (TICKER_INDICE, e))
        indice_par_date = {}

    lot = ([(t, actions[t], nb_titres.get(t)) for t in sorted(actions)]
           + [(t, t, None) for t in sorted(indices)])
    if sys.argv[1:]:
        voulus = {t.upper() for t in sys.argv[1:]}
        lot = [x for x in lot if x[0].upper() in voulus]

    manquants = [t for t, _, n in lot if n is None and t in actions]
    if manquants:
        print("Nombre de titres inconnu pour %s : valorisation laissee vide."
              " Relancer faire_actions.py.\n" % ", ".join(manquants))

    ok, echecs, modifies = 0, [], 0
    for i, (t, url_t, n) in enumerate(lot, 1):
        try:
            valeurs, jour = calculer(t, url_t, n, indice_par_date)
        except Exception as e:
            print("  [%2d/%2d] %-8s ECHEC %s" % (i, len(lot), t, e))
            echecs.append((t, str(e)))
            continue
        change = ecrire(t, valeurs)
        modifies += 1 if change else 0
        print("  [%2d/%2d] %-8s OK   %s  cours=%-10s var=%-8s RSI=%-6s beta=%-6s%s"
              % (i, len(lot), t, jour, valeurs["Cours_Actuel"],
                 valeurs["Variation_Cours"] or "-", valeurs["RSI"] or "-",
                 valeurs["Beta_1_An"] or "-", "" if change else "  (inchange)"))
        ok += 1

    print("\n%d/%d calcules, %d fichier(s) modifie(s)" % (ok, len(lot), modifies))
    if echecs:
        print("Echecs :")
        for t, m in echecs:
            print("   %-8s %s" % (t, m))

    # Meme garde-fou que la collecte : echouer bruyamment plutot que publier
    # un jeu d'indicateurs a moitie vide, que le MCP lirait sans broncher.
    if ok < len(lot) / 2:
        print("\nALERTE : plus de la moitie des tickers en echec.")
        sys.exit(1)


if __name__ == "__main__":
    main()
