"""
Package de tests pour l'application Dream Journal.

Ce package contient tous les tests organisés par domaine :

├── test_core.py        - Fonctionnalités de base
├── test_models.py      - Persistance des données  
├── test_utils.py       - Fonctions utilitaires et statistiques
├── test_ai_functions.py- Intégrations IA (mockées)
├── test_views.py       - Tests des vues Django
├── test_security.py    - Tests de sécurité (XSS, SQLi, permissions)
└── test_integration.py - Workflow complet

Commandes utiles :

# Lancer tous les tests
python manage.py test diary.tests

# Lancer un fichier de tests spécifique
python manage.py test diary.tests.test_core

# Verbosité
python manage.py test diary.tests -v 2

# Tests parallèles
python manage.py test diary.tests --parallel

# Couverture
coverage run --source='.' manage.py test diary.tests
coverage report -m
coverage html
"""

# Import des modules de tests (facilite python manage.py test diary.tests)
from .test_core import *
from .test_models import *
from .test_utils import *
from .test_ai_functions import *
from .test_views import *
from .test_security import *
from .test_integration import *

# Indicateur de configuration de test
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "DreamProject.settings")
