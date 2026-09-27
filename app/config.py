"""Reglages lus dans les variables d'environnement (fichier .env en local,
onglet Environment sur Render) et dans le catalogue modeles.json.
Aucun secret dans le code."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _obligatoire(nom: str) -> str:
    valeur = os.environ.get(nom, "").strip()
    if not valeur:
        raise RuntimeError(
            "Variable d'environnement %s manquante. Voir app/.env.example." % nom
        )
    return valeur


# Supabase. La cle "publishable" (ou l'ancienne cle "anon") est publique par
# conception : c'est la RLS qui protege les donnees, pas le secret de la cle.
SUPABASE_URL = _obligatoire("SUPABASE_URL").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = _obligatoire("SUPABASE_PUBLISHABLE_KEY")

# Serveur MCP des donnees de marche.
MCP_URL = os.environ.get("MCP_URL", "https://gomycode-1.onrender.com/mcp").strip()

# Modeles : catalogue modeles.json, cles dans l'environnement. Un fournisseur
# sans cle est ignore, et ses modeles n'apparaissent pas dans le menu.
_catalogue = json.loads((Path(__file__).parent / "modeles.json").read_text(encoding="utf-8"))
FOURNISSEURS = {
    nom: {**f, "base_url": f["base_url"].rstrip("/"), "api_key": os.environ.get(f["cle"], "").strip()}
    for nom, f in _catalogue["fournisseurs"].items()
}
MODELES = [
    {**m, "base_url": FOURNISSEURS[m["fournisseur"]]["base_url"],
     "api_key": FOURNISSEURS[m["fournisseur"]]["api_key"]}
    for m in _catalogue["modeles"]
    if FOURNISSEURS[m["fournisseur"]]["api_key"] or FOURNISSEURS[m["fournisseur"]].get("sans_cle")
]
if not MODELES:
    raise RuntimeError("Aucun modele utilisable : definir au moins une cle de modeles.json "
                       "(%s) dans le .env." % ", ".join(f["cle"] for f in FOURNISSEURS.values()))
MODELE_PAR_DEFAUT = MODELES[0]["id"]


def chaine(modele_id: str | None) -> list[dict]:
    """Le modele demande, puis ses secours. Par defaut, les secours sont les
    autres modeles du meme fournisseur, dans l'ordre du catalogue ; un modele
    peut aussi les nommer lui-meme ("secours" dans modeles.json). Un secours
    sans cle est ignore. Un identifiant inconnu donne le modele par defaut."""
    choisi = next((m for m in MODELES if m["id"] == modele_id), MODELES[0])
    if "secours" in choisi:
        par_id = {m["id"]: m for m in MODELES}
        secours = [par_id[i] for i in choisi["secours"] if i in par_id and i != choisi["id"]]
    else:
        secours = [m for m in MODELES if m["fournisseur"] == choisi["fournisseur"] and m is not choisi]
    return [choisi] + secours


def catalogue_public() -> list[dict]:
    """Ce que la page affiche du catalogue : jamais les cles ni les URL."""
    return [
        {"id": m["id"], "nom": m["nom"], "note": m.get("note", ""), "fournisseur": m["fournisseur"],
         "fournisseur_nom": FOURNISSEURS[m["fournisseur"]]["nom"],
         "logo": FOURNISSEURS[m["fournisseur"]].get("logo"),
         "couleur": FOURNISSEURS[m["fournisseur"]].get("couleur")}
        for m in MODELES
    ]
