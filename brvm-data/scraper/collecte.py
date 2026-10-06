# -*- coding: utf-8 -*-
"""Phase 1 — collecte des cours BRVM depuis richbourse.

Pour chaque ticker, recupere l'historique complet en JSON et ecrit cinq CSV :
  data/{T}/{T}.daily.csv      une ligne par seance
  data/{T}/{T}.weekly.csv     agrege, semaine etiquetee au lundi de fin
  data/{T}/{T}.monthly.csv    agrege, dernier jour du mois
  data/{T}/{T}.quarterly.csv  agrege, dernier jour du trimestre
  data/{T}/{T}.yearly.csv     agrege, 31 decembre

Traite aussi les 18 indices (BRVMC, BRVM30, ...), qui passent par un autre
endpoint et dont la reponse porte la cle `cours` au lieu de `ohlc`. La serie du
BRVM Composite est indispensable : c'est la reference du Beta calcule en
phase 2, et le seul etalon de performance du portefeuille.

Aucune dependance externe : uniquement la bibliotheque standard.

Usage :
    python collecte.py              actions + indices
    python collecte.py NSBC BRVMC   seulement ceux-la
    python collecte.py --leger      passage leger : trois requetes de controle, la collecte complete
                                    seulement si la source a du nouveau (workflow, toutes les 15 min)
"""
import csv, datetime, io, json, os, sys, time, urllib.error, urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER_DATA = os.path.join(RACINE, "data")
TICKERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickers.json")
INDICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "indices.json")

BASE = "https://www.richbourse.com/common/mouvements/technique-donnees"
BASE_INDICE = "https://www.richbourse.com/common/mouvements/indice-donnees"
ENTETES = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}
PAUSE = 0.6          # secondes entre deux tickers
ESSAIS = 3           # tentatives par ticker
MIN_SEANCES = 2      # en dessous, on considere la reponse suspecte
ECHECS_DE_SUITE = 8  # apres autant d'echecs d'affilee, la source refuse nos appels : on n'insiste pas
# Passage leger : les titres les plus echanges. Si la source a du nouveau, ils le montrent les premiers.
SONDES = ["SNTS", "ORAC", "SGBC"]

COLONNES = ["Date", "Open", "High", "Low", "Close", "Volume"]


# ----------------------------------------------------------------- reseau

def recuperer(ticker, alias=None):
    """Retourne [(date, o, h, l, c, v), ...] trie par date, ou leve une exception.

    `alias` non nul = indice : autre endpoint, autre parametre, autre page de
    reference. Les indices n'ont ni volume ni OHLC, seulement un point de
    cloture par seance.
    """
    if alias:
        url = "%s?alias_indice=%s&complet=1" % (BASE_INDICE, alias)
        referer = "https://www.richbourse.com/common/mouvements/indice/%s" % alias
    else:
        url = "%s?symbole=%s&complet=1" % (BASE, ticker)
        referer = "https://www.richbourse.com/common/mouvements/technique/%s" % ticker
    entetes = dict(ENTETES)
    entetes["Referer"] = referer

    derniere = None
    for essai in range(1, ESSAIS + 1):
        try:
            req = urllib.request.Request(url, headers=entetes)
            with urllib.request.urlopen(req, timeout=40) as r:
                ctype = r.headers.get("Content-Type", "")
                brut = r.read()
            if "json" not in ctype:
                # Diagnostic : savoir SI on est bloque, et par quoi.
                apercu = brut[:180].decode("utf-8", "replace").replace("\n", " ")
                raise ValueError("HTTP 200 mais type=%s | debut: %s"
                                 % (ctype[:40], apercu))
            d = json.loads(brut.decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            corps = ""
            try:
                corps = e.read()[:180].decode("utf-8", "replace").replace("\n", " ")
            except Exception:
                pass
            derniere = ValueError("HTTP %s %s | %s" % (e.code, e.reason, corps))
            if essai < ESSAIS:
                time.sleep(2 * essai)
        except Exception as e:
            derniere = e
            if essai < ESSAIS:
                time.sleep(2 * essai)
    else:
        raise derniere

    cours = d.get("ohlc") or d.get("cours") or []
    volumes = {int(x[0]): x[1] for x in (d.get("volume") or []) if x and x[0] is not None}

    lignes = []
    for ligne in cours:
        if not ligne or ligne[0] is None:
            continue
        ts = int(ligne[0])
        jour = datetime.datetime.fromtimestamp(ts / 1000, datetime.UTC).date()
        if len(ligne) >= 5:
            o, h, b, c = ligne[1], ligne[2], ligne[3], ligne[4]
        else:                      # indices : [timestamp, cours]
            o = h = b = c = ligne[1]
        if c is None:
            continue
        lignes.append((jour, o, h, b, c, volumes.get(ts, 0)))

    lignes.sort(key=lambda x: x[0])
    return lignes


# ------------------------------------------------------------- agregation

def fin_semaine(d):
    """Lundi qui cloture la semaine, convention des anciens fichiers."""
    return d + datetime.timedelta(days=(7 - d.weekday()) % 7)


def fin_mois(d):
    if d.month == 12:
        return datetime.date(d.year, 12, 31)
    return datetime.date(d.year, d.month + 1, 1) - datetime.timedelta(days=1)


def fin_trimestre(d):
    m = ((d.month - 1) // 3 + 1) * 3
    return fin_mois(datetime.date(d.year, m, 1))


def fin_annee(d):
    return datetime.date(d.year, 12, 31)


def agreger(lignes, cle):
    """Regroupe les seances par periode. Open = premiere, Close = derniere,
    High = max, Low = min, Volume = somme."""
    paquets = {}
    ordre = []
    for jour, o, h, b, c, v in lignes:
        etiquette = cle(jour)
        if etiquette not in paquets:
            paquets[etiquette] = [o, h, b, c, v or 0]
            ordre.append(etiquette)
        else:
            p = paquets[etiquette]
            if h is not None:
                p[1] = h if p[1] is None else max(p[1], h)
            if b is not None:
                p[2] = b if p[2] is None else min(p[2], b)
            p[3] = c
            p[4] += (v or 0)
    return [(e, *paquets[e]) for e in sorted(ordre)]


# ---------------------------------------------------------------- ecriture

def nombre(x):
    """Entier si la valeur est entiere, sinon flottant. Vide si None."""
    if x is None:
        return ""
    f = float(x)
    return str(int(f)) if f.is_integer() else repr(f)


def ecrire(chemin, lignes):
    """Ecrit le CSV. Retourne True si le contenu a change, False sinon.

    Ne pas reecrire un fichier identique evite des commits vides et garde
    l'historique git lisible.
    """
    tampon = io.StringIO()
    w = csv.writer(tampon, lineterminator="\n")
    w.writerow(COLONNES)
    for jour, o, h, b, c, v in lignes:
        w.writerow([jour.isoformat(), nombre(o), nombre(h), nombre(b),
                    nombre(c), nombre(v)])
    contenu = tampon.getvalue()

    if os.path.exists(chemin):
        with open(chemin, encoding="utf-8") as f:
            if f.read() == contenu:
                return False

    with open(chemin, "w", newline="", encoding="utf-8") as f:
        f.write(contenu)
    return True


def traiter(ticker, alias=None):
    """Retourne (ok, message, derniere_date)."""
    try:
        lignes = recuperer(ticker, alias)
    except Exception as e:
        return False, "reseau : %s" % e, None

    if len(lignes) < MIN_SEANCES:
        return False, "seulement %d seance(s), on ne remplace rien" % len(lignes), None

    dossier = os.path.join(DOSSIER_DATA, ticker)
    os.makedirs(dossier, exist_ok=True)

    jeux = {
        "daily": lignes,
        "weekly": agreger(lignes, fin_semaine),
        "monthly": agreger(lignes, fin_mois),
        "quarterly": agreger(lignes, fin_trimestre),
        "yearly": agreger(lignes, fin_annee),
    }
    modifies = 0
    for periode, jeu in jeux.items():
        if ecrire(os.path.join(dossier, "%s.%s.csv" % (ticker, periode)), jeu):
            modifies += 1

    marque = "" if modifies else "  (inchange)"
    return True, "%5d seances, du %s au %s%s" % (
        len(lignes), lignes[0][0], lignes[-1][0], marque), lignes[-1][0]


# -------------------------------------------------------------------- main

def ligne_csv(seance):
    """Une seance telle que ecrire() l'ecrit dans le CSV (pour la comparer a celle qu'on a deja)."""
    jour, o, h, b, c, v = seance
    return ",".join([jour.isoformat(), nombre(o), nombre(h), nombre(b), nombre(c), nombre(v)])


def derniere_seance_locale(ticker):
    """La derniere ligne de data/{T}/{T}.daily.csv, ou None s'il n'y en a pas."""
    chemin = os.path.join(DOSSIER_DATA, ticker, "%s.daily.csv" % ticker)
    try:
        with open(chemin, encoding="utf-8") as f:
            lignes = [l.rstrip("\n") for l in f if l.strip()]
    except OSError:
        return None
    return lignes[-1] if len(lignes) > 1 else None


def sonder():
    """Passage leger : une requete par sonde, sa derniere seance comparee a celle qu'on a deja.
    Retourne (sondes ou la source a du nouveau, sondes en erreur)."""
    nouveau, erreurs = [], []
    for i, t in enumerate(SONDES):
        try:
            lignes = recuperer(t)
            if len(lignes) < MIN_SEANCES:
                raise ValueError("seulement %d seance(s)" % len(lignes))
        except Exception as e:
            erreurs.append((t, str(e)[:160]))
            print("  sonde %-5s ECHEC : %s" % (t, str(e)[:160]))
            continue
        recue = ligne_csv(lignes[-1])
        change = recue != derniere_seance_locale(t)
        print("  sonde %-5s %s : %s" % (t, "NOUVEAU" if change else "identique", recue))
        if change:
            nouveau.append(t)
        if i < len(SONDES) - 1:
            time.sleep(PAUSE)
    return nouveau, erreurs


def diagnostic():
    """Avant de lancer 49 requetes, dire d'ou l'on appelle et si la source
    accepte l'appel. Sans cela, un blocage d'adresse IP ressemble a une panne
    du collecteur."""
    print("--- diagnostic ---")
    try:
        req = urllib.request.Request("https://api.ipify.org?format=json",
                                     headers={"User-Agent": ENTETES["User-Agent"]})
        with urllib.request.urlopen(req, timeout=15) as r:
            print("  adresse publique : %s" % json.loads(r.read()).get("ip"))
    except Exception as e:
        print("  adresse publique : inconnue (%s)" % e)

    url = "%s?symbole=NSBC&complet=1" % BASE
    entetes = dict(ENTETES)
    entetes["Referer"] = "https://www.richbourse.com/common/mouvements/technique/NSBC"
    try:
        req = urllib.request.Request(url, headers=entetes)
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = r.headers.get("Content-Type", "")
            taille = len(r.read())
        print("  test richbourse  : HTTP %s | %s | %d octets" % (r.status, ctype[:40], taille))
        if "json" not in ctype:
            print("  >>> la source ne rend pas du JSON : appel probablement bloque")
    except urllib.error.HTTPError as e:
        corps = ""
        try:
            corps = e.read()[:200].decode("utf-8", "replace").replace("\n", " ")
        except Exception:
            pass
        print("  test richbourse  : HTTP %s %s" % (e.code, e.reason))
        print("  corps            : %s" % corps)
        print("  >>> appel refuse par la source")
    except Exception as e:
        print("  test richbourse  : echec %s : %s" % (type(e).__name__, e))
    print("--- fin diagnostic ---\n")


def main():
    leger = "--leger" in sys.argv[1:]
    arguments = [a for a in sys.argv[1:] if a != "--leger"]
    actions = json.load(open(TICKERS, encoding="utf-8"))["actions"]
    indices = json.load(open(INDICES, encoding="utf-8"))["indices"]

    # Chaque entree est un couple (ticker, alias) ; alias None pour une action.
    lot = ([(t, None) for t in sorted(actions)]
           + [(t, indices[t]) for t in sorted(indices)])
    if arguments:
        voulus = {t.upper() for t in arguments}
        lot = [x for x in lot if x[0].upper() in voulus]
        inconnus = voulus - {x[0].upper() for x in lot}
        if inconnus:
            print("Inconnu(s), ignore(s) : %s" % ", ".join(sorted(inconnus)))
    demandes = lot

    os.makedirs(DOSSIER_DATA, exist_ok=True)
    if leger:
        # Une collecte complete, c'est 67 requetes qui rechargent tout l'historique : toutes les 15 minutes, la source finit par
        # refuser nos appels. On regarde d'abord, en trois requetes, s'il y a du nouveau.
        print("Passage leger : %d sondes\n" % len(SONDES))
        nouveau, erreurs = sonder()
        if len(erreurs) == len(SONDES):
            print("\nLa source ne repond a aucune sonde : on reessaiera au prochain passage.")
            diagnostic()
            sys.exit(1)
        if not nouveau:
            print("\nRien de nouveau : on ne collecte pas.")
            return
        print("\nDu nouveau (%s) : collecte complete.\n" % ", ".join(nouveau))
    else:
        diagnostic()
    print("Collecte de %d ticker(s)\n" % len(demandes))

    ok, echecs, dates = 0, [], []
    de_suite = 0
    for i, (t, alias) in enumerate(demandes, 1):
        reussi, message, derniere = traiter(t, alias)
        etat = "OK  " if reussi else "ECHEC"
        print("  [%2d/%2d] %-8s %s %s" % (i, len(demandes), t, etat, message))
        if reussi:
            ok += 1
            de_suite = 0
            # Seules les actions comptent pour le controle de fraicheur : les
            # sept indices d'avant la reforme sont geles au 31/12/2025, ils
            # feraient echouer le job tous les jours.
            if derniere and alias is None:
                dates.append(derniere)
        else:
            echecs.append((t, message))
            de_suite += 1
            if de_suite >= ECHECS_DE_SUITE:
                print("\nARRET : %d echecs d'affilee, la source refuse probablement nos appels. On n'insiste pas." % de_suite)
                break
        if i < len(demandes):
            time.sleep(PAUSE)

    print("\n%d/%d reussis" % (ok, len(demandes)))
    if echecs:
        print("Echecs :")
        for t, m in echecs:
            print("   %-6s %s" % (t, m))

    # --- garde-fous : le job doit echouer bruyamment ---------------------

    if ok < len(demandes) / 2:
        print("\nALERTE : plus de la moitie des tickers en echec, collecte cassee.")
        sys.exit(1)

    if dates:
        plus_recente = max(dates)
        aujourdhui = datetime.datetime.now(datetime.UTC).date()
        ouvres = 0
        j = plus_recente
        while j < aujourdhui:
            j += datetime.timedelta(days=1)
            if j.weekday() < 5:
                ouvres += 1
        print("Seance la plus recente collectee : %s (%d jour(s) ouvre(s) de retard)"
              % (plus_recente, ouvres))
        if ouvres > 3:
            print("\nALERTE : aucune donnee fraiche depuis plus de 3 jours ouvres.")
            print("La source a probablement change. Ne pas faire confiance aux CSV.")
            sys.exit(1)


if __name__ == "__main__":
    main()
