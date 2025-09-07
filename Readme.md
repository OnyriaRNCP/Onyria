# Onyria – DreamProject

**Application de journal de rêves intelligent** utilisant l'IA pour analyser et enrichir vos rêves avec Django et Tailwind CSS.

## Structure du projet

```
ONYRIA/
├── DreamProject/            # Application Django principale
│   ├── diary/              # App principale (journal de rêves)
│   │   └── tests/          # Tests unitaires complets
│   ├── accounts/           # Gestion des utilisateurs
│   │   └── tests/          # Tests unitaires complets
│   └── Onyria/            # Configuration Django
├── frontend/               # Assets Tailwind CSS
├── static/                 # Fichiers statiques compilés
├── .env                    # Variables d'environnement (local)
├── .gitignore             # Fichiers ignorés par Git
├── requirements.txt        # Dépendances Python
├── package.json           # Dépendances Node.js
└── tailwind.config.js     # Configuration Tailwind
```

## Prérequis

- **Python** ≥ 3.11
- **Node.js** ≥ 18.x (LTS recommandé)
- **npm** ≥ 9.x

## Variables d'environnement

Le projet utilise des variables d'environnement pour la configuration. Créer un fichier `.env` à la racine du projet :

```bash
# Configuration Django
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
APP_ENV=dev

# Base de données
DATABASE_URL=sqlite:///db.sqlite3

# Sécurité CSRF
CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

# APIs IA (obligatoires)
GROQ_API_KEY=your-groq-api-key-here
MISTRAL_API_KEY=your-mistral-api-key-here
```

### Environnements

Le projet est configuré avec 3 environnements :

- **Dev** (branche principale) : Développement local avec `.env`
- **pre-production** : Environnement de test sur Render
- **Production** : Environnement de production sur Render

**Important** : Ne jamais committer le fichier `.env` ! Il est déjà dans `.gitignore`.

## Installation locale

### Backend (Django)

**Créer et activer un environnement virtuel :**

```bash
# Créer l'environnement virtuel
python -m venv .venv

# Activer l'environnement virtuel
# Sur Windows
.venv\Scripts\activate
# Sur macOS/Linux
source .venv/bin/activate
```

Cloner le dépôt et installer les dépendances Python :

```bash
pip install -r requirements.txt
```

Appliquer les migrations :

```bash
python manage.py migrate
```

Lancer le serveur local :

```bash
python manage.py runserver
```

**L'application sera accessible sur** : http://localhost:8000

### Créer un superutilisateur

```bash
python manage.py createsuperuser
```

### Frontend (Tailwind CSS)

Installer les dépendances Node :

```bash
npm ci --include=dev
```

## Compilation du CSS Tailwind

Le CSS Tailwind est défini dans `frontend/tailwind.css` et compilé vers `static/css/tailwind.min.css`.

### Compilation unique (avant déploiement)

```bash
npm run build:css
```

### Compilation automatique (mode développement)

```bash
npm run watch:css
```

--> Le fichier généré `static/css/tailwind.min.css` est ignoré par Git (via `.gitignore`) et ne doit **pas** être committé.

## Configuration Tailwind

- **Fichier** : `tailwind.config.js`
- **Chemins scannés** :
  - `DreamProject/**/*.html`
  - `diary/templates/**/*.html`
  - `accounts/templates/**/*.html`
  - `frontend/**/*.js`
  - `js/**/*.js`

--> Les classes inutilisées sont automatiquement purgées.

## Intégration Django

Dans `base.html`, le fichier compilé est inclus via :

```django
{% load static %}
<link rel="stylesheet" href="{% static 'css/tailwind.min.css' %}">
```

--> Django ne compile pas Tailwind. Il ne fait que charger le fichier généré par npm.

## Applications Django

- `diary`
- `accounts`

## Tests

Le projet dispose d'une suite de tests complète organisée par modules dans les deux applications :

### Lancer les tests

```bash
# Tests complets des deux apps
python manage.py test

# Tests par application
python manage.py test diary
python manage.py test accounts

# Tests spécifiques par module (diary)
python manage.py test diary.tests.test_models
python manage.py test diary.tests.test_views
python manage.py test diary.tests.test_security
python manage.py test diary.tests.test_ai_functions
python manage.py test diary.tests.test_core
python manage.py test diary.tests.test_utils
python manage.py test diary.tests.test_integration

# Tests spécifiques par module (accounts)
python manage.py test accounts.tests.test_models
python manage.py test accounts.tests.test_views
python manage.py test accounts.tests.test_forms
python manage.py test accounts.tests.test_security
python manage.py test accounts.tests.test_core
```

### Tests automatisés avec GitHub Actions

- **Tests à chaque push** : Vérifications rapides + formatage Black
- **Tests quotidiens** : Suite complète avec couverture de code
- **Tests hebdomadaires** : Tests complets + audit sécurité (Bandit, Safety)
- **Protection des branches** : Workflow Dev → pre-production → Production

## Workflow de déploiement

### Environnements

1. **Dev** (branche principale) → Développement local
2. **pre-production** → Environnement de test sur Render  
3. **Production** → Environnement de production sur Render

### Règles de merge

- `Dev` → `pre-production` uniquement
- `pre-production` → `Production` uniquement
- Protection automatique via GitHub Actions

## Déploiement

En production, l'application est servie avec Gunicorn (mode ASGI via UvicornWorker) et le CSS Tailwind doit être compilé avant le lancement.

### Exemple

```bash
pip install -r requirements.txt
npm ci --include=dev
npm run build:css
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn Onyria.asgi:application -k uvicorn.workers.UvicornWorker --chdir DreamProject --bind 0.0.0.0:$PORT
```
--> Gunicorn (avec UvicornWorker) exécute l'application Django en mode ASGI, ce qui permet de gérer correctement les connexions longues (ex. SSE).

--> WhiteNoise permet de servir directement les fichiers statiques, sans avoir besoin d'un serveur web externe (NGINX, Apache…).

## Fichiers statiques

En développement, les fichiers statiques (CSS, JS, images) sont servis automatiquement par Django.

En production, il faut les collecter avant le lancement :

```bash
python manage.py collectstatic --noinput
```

Cela rassemble tous les fichiers dans le dossier défini par `STATIC_ROOT` (par défaut `staticfiles/`).

Le projet utilise WhiteNoise pour gérer ces fichiers statiques en production.
