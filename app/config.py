"""Reglages lus dans les variables d'environnement (fichier .env en local,
onglet Environment sur Render). Aucun secret dans le code."""
import os

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

# Modele de langage : n'importe quel fournisseur compatible OpenAI
# (/chat/completions). Changer de modele = changer ces trois lignes.
LLM_BASE_URL = _obligatoire("LLM_BASE_URL").rstrip("/")
# Un modele, ou plusieurs separes par des virgules : les suivants servent de
# secours quand le premier est sature ou hors quota.
LLM_MODELS = [m.strip() for m in _obligatoire("LLM_MODEL").split(",") if m.strip()]
LLM_MODEL = LLM_MODELS[0]
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()   # vide pour Ollama en local

# Serveur MCP des donnees de marche.
MCP_URL = os.environ.get("MCP_URL", "https://gomycode-1.onrender.com/mcp").strip()
