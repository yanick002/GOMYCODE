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
| `modeles.json` | catalogue des modèles proposés dans le menu |
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

### 2. Les modèles

Mettre dans `.env` la clé d'au moins un fournisseur déclaré dans `modeles.json`
(`GEMINI_API_KEY`, `NVIDIA_API_KEY`…). Les modèles d'un fournisseur sans clé
n'apparaissent pas dans le menu.

### 3. Lancer en local

```bash
cd app
cp .env.example .env      # puis remplir les valeurs
pip install -r requirements.txt
python main.py            # http://localhost:8080
```

## Choisir et ajouter des modèles

L'utilisateur choisit le modèle dans le menu de la zone de saisie ; son choix
est retenu par le navigateur. La réponse affiche le logo et le nom du modèle
qui a réellement répondu : si le modèle choisi est saturé ou hors quota, la
question repart avec les autres modèles **du même fournisseur**.

Le catalogue est `modeles.json`, sans toucher au code :

- **Ajouter un modèle** : une ligne dans `modeles`, avec son identifiant exact
  chez le fournisseur. Il doit savoir appeler des outils (function calling).
- **Ajouter un fournisseur** : une entrée dans `fournisseurs` avec son URL
  compatible OpenAI, le nom de la variable de sa clé, un logo (fichier SVG
  dans `static/logos/`, par exemple depuis [Simple Icons](https://simpleicons.org))
  et sa couleur. Puis la clé dans `.env`.
- **Modèle par défaut** : le premier de la liste dont la clé est définie.

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
