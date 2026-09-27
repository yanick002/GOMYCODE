# Portefeuille BRVM — application web

Chaque utilisateur crée un compte, saisit son portefeuille et pose ses questions.
Un modèle open source répond en s'appuyant sur le portefeuille et sur les
données de marché du serveur MCP.

```
Navigateur ── FastAPI (ce dossier) ──┬── Supabase : comptes + portefeuilles
                                     ├── Modèle (tout fournisseur compatible OpenAI)
                                     └── MCP BRVM : cours, RSI, historique
```

| Fichier | Rôle |
|---|---|
| `main.py` | routes web, flux des réponses |
| `base.py` | Supabase : vérification du compte, lecture et écriture du portefeuille |
| `portefeuille.py` | valorisation (valeur, plus-value, poids), en Python |
| `marche.py` | connexion au MCP : cours du jour, outils pour le modèle |
| `agent.py` | boucle question → modèle → outils → réponse |
| `static/index.html` | la page |
| `schema.sql` | tables et règles d'accès Supabase |

## Mise en route

### 1. Supabase

1. Créer un projet sur [supabase.com](https://supabase.com).
2. **SQL Editor** → New query → coller `schema.sql` → **Run**.
3. **Project Settings → API** : noter l'URL du projet et la clé **publishable**
   (ou l'ancienne clé **anon**).
4. **Authentication → URL Configuration** : mettre l'adresse de l'application
   dans **Site URL** (`http://localhost:8080` en local, l'adresse Render ensuite).
   C'est vers elle que pointe le lien de confirmation envoyé par e-mail.

### 2. Le modèle

Créer une clé chez un fournisseur compatible OpenAI (Groq, OpenRouter,
Together…) et choisir un modèle qui sait **appeler des outils**
(« tool use » ou « function calling » dans la liste du fournisseur).

### 3. Lancer en local

```bash
cd app
cp .env.example .env      # puis remplir les valeurs
pip install -r requirements.txt
python main.py            # http://localhost:8080
```

## Changer de modèle

Modifier trois variables, sans toucher au code, puis redémarrer :

```
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=<nom exact du modèle chez ce fournisseur>
LLM_API_KEY=<clé>
```

Le nom du modèle en cours s'affiche en haut de la page.

## Déployer sur Render

Web Service, même dépôt que le MCP :

| Champ | Valeur |
|---|---|
| Root Directory | `app` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python main.py` |
| Health Check Path | `/health` |
| Environment | les variables de `.env.example`, avec leurs valeurs |

## Choix qui comptent

- **Les calculs sont faits en Python, pas par le modèle.** Il reçoit valeur,
  plus-value et poids déjà calculés et les interprète.
- **Le portefeuille ne passe jamais par le MCP**, qui est public. Il reste entre
  l'application et Supabase.
- **La base est interrogée avec le jeton de l'utilisateur**, jamais avec une clé
  d'administration. La Row Level Security de `schema.sql` empêche donc un compte
  de lire les données d'un autre, même en cas de bug dans l'application.
- **Un titre sans cours n'est jamais compté pour zéro.** Il est signalé à part.
