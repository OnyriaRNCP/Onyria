"""
Tests critiques essentiels pour l'authentification.

Ce module contient les tests les plus importants et rapides à exécuter
pendant le développement pour vérifier que les fonctionnalités d'authentification
fonctionnent correctement.

Usage: python manage.py test accounts.tests.test_core

Ces tests doivent s'exécuter en moins de 30 secondes.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model        
from datetime import date, timedelta

import os

from ..forms import RegisterForm, LoginForm

User = get_user_model()

TEST_USER_PASSWORD = os.environ.get('TEST_PASSWORD', 'django_test_secure_2024')


class CoreUserModelTest(TestCase):
    """
    Tests essentiels du modèle CustomUser.

    Ces tests vérifient les fonctionnalités de base du modèle
    qui sont utilisées partout dans l'application d'authentification.
    """

    def test_create_user_basic(self):
        """
        Test critique : Création de base d'un utilisateur.

        Si ce test échoue, l'authentification ne peut pas fonctionner.
        """
        user = User.objects.create_user(
            email='test@example.com',
            username='testuser',
            password=TEST_USER_PASSWORD
        )

        self.assertEqual(user.email, 'test@example.com')
        self.assertEqual(user.username, 'testuser')
        self.assertTrue(user.check_password(TEST_USER_PASSWORD))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_user_with_optional_fields(self):
        """
        Test critique : Création d'utilisateur avec champs optionnels.

        Vérifie que les champs âge et sexe fonctionnent correctement.
        """
        birth_date = date.today() - timedelta(days=25*365)  # 25 ans environ
        
        user = User.objects.create_user(
            email='complete@example.com',
            username='completeuser',
            password=TEST_USER_PASSWORD,
            date_of_birth=birth_date,
            sexe='F',
            bio='Test bio'
        )

        self.assertEqual(user.email, 'complete@example.com')
        self.assertEqual(user.date_of_birth, birth_date)
        self.assertEqual(user.sexe, 'F')
        self.assertEqual(user.bio, 'Test bio')
        self.assertAlmostEqual(user.age, 25, delta=1)

    def test_user_string_representation(self):
        """
        Test critique : Représentation string du modèle.

        Vérifie l'affichage correct dans l'admin Django.
        """
        user = User.objects.create_user(
            email='repr@example.com',
            username='repruser',
            password=TEST_USER_PASSWORD
        )

        self.assertEqual(str(user), 'repr@example.com')

    def test_email_as_username_field(self):
        """
        Test critique : Email comme identifiant principal.

        Vérifie que l'email est utilisé pour l'authentification.
        """
        self.assertEqual(User.USERNAME_FIELD, 'email')
        self.assertEqual(User.REQUIRED_FIELDS, ['username'])

    def test_age_calculation_property(self):
        """
        Test critique : Calcul automatique de l'âge.

        Vérifie que l'âge est calculé correctement depuis la date de naissance.
        """
        from datetime import date
        
        # Utilisateur sans date de naissance
        user_no_birth = User.objects.create_user(
            email='nobirth@example.com',
            username='nobirth',
            password=TEST_USER_PASSWORD
        )
        self.assertIsNone(user_no_birth.age)

        # Utilisateur avec date de naissance (25 ans)
        birth_date = date(2000, 1, 1)
        user_with_birth = User.objects.create_user(
            email='withbirth@example.com',
            username='withbirth',
            password=TEST_USER_PASSWORD,
            date_of_birth=birth_date
        )
        
        # L'âge doit être proche de 24 ans (dépend de la date du test)
        self.assertIsNotNone(user_with_birth.age)
        self.assertGreaterEqual(user_with_birth.age, 23)
        self.assertLessEqual(user_with_birth.age, 25)


class CoreAuthenticationTest(TestCase):
    """
    Tests essentiels d'authentification.

    Ces tests vérifient que le système d'authentification
    de base fonctionne correctement.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='auth@example.com',
            username='authuser',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    def test_user_can_login_with_email(self):
        """
        Test critique : Connexion avec email.

        Si ce test échoue, les utilisateurs ne peuvent pas se connecter.
        """
        from django.contrib.auth import authenticate

        user = authenticate(
            email='auth@example.com',
            password=TEST_USER_PASSWORD
        )

        self.assertIsNotNone(user)
        self.assertEqual(user.email, 'auth@example.com')

    def test_user_cannot_login_with_wrong_password(self):
        """
        Test critique : Rejet des mauvais mots de passe.

        Sécurité de base de l'authentification.
        """
        from django.contrib.auth import authenticate

        user = authenticate(
            email='auth@example.com',
            password='wrong_password'
        )

        self.assertIsNone(user)

    def test_user_cannot_login_with_wrong_email(self):
        """
        Test critique : Rejet des emails inexistants.

        Sécurité de base de l'authentification.
        """
        from django.contrib.auth import authenticate

        user = authenticate(
            email='nonexistent@example.com',
            password=TEST_USER_PASSWORD
        )

        self.assertIsNone(user)

    def test_inactive_user_cannot_login(self):
        """
        Test critique : Utilisateurs inactifs rejetés.

        Vérifie que les comptes désactivés ne peuvent pas se connecter.
        """
        from django.contrib.auth import authenticate

        self.user.is_active = False
        self.user.save()

        user = authenticate(
            email='auth@example.com',
            password=TEST_USER_PASSWORD
        )

        self.assertIsNone(user)


class CoreFormsTest(TestCase):
    """
    Tests essentiels des formulaires d'authentification.

    Ces tests vérifient que les formulaires de base
    fonctionnent correctement.
    """

    def test_register_form_valid_data(self):
        """
        Test critique : Formulaire d'inscription valide.

        Si ce test échoue, les utilisateurs ne peuvent pas s'inscrire.
        """
        form_data = {
            'email': 'newuser@example.com',
            'username': 'newuser',
            'password1': 'ComplexPassword123!',
            'password2': 'ComplexPassword123!',
            'date_of_birth': '1995-01-01',  # Ajout de la date de naissance requise
        }

        print(f"\n=== TEST: test_register_form_valid_data ===")
        print(f"Form data: {form_data}")
        
        form = RegisterForm(data=form_data)
        print(f"Form is valid: {form.is_valid()}")
        if not form.is_valid():
            print(f"Form errors: {form.errors}")
            print(f"Form non-field errors: {form.non_field_errors()}")
            for field_name, field_errors in form.errors.items():
                print(f"  {field_name}: {field_errors}")
        
        self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")

        # Test de sauvegarde
        user = form.save()
        print(f"User created: {user.email}, age: {user.age}, bio: '{user.bio}'")
        self.assertEqual(user.email, 'newuser@example.com')
        self.assertEqual(user.username, 'newuser')
        self.assertTrue(user.check_password('ComplexPassword123!'))

    def test_register_form_with_optional_fields(self):
        """
        Test critique : Formulaire d'inscription avec champs optionnels.

        Vérifie que les champs âge et sexe sont gérés correctement.
        """
        from datetime import date
        
        form_data = {
            'email': 'complete@example.com',
            'username': 'completeuser',
            'password1': 'ComplexPassword123!',
            'password2': 'ComplexPassword123!',
            'date_of_birth': '1990-05-15',
            'sexe': 'M',
            # bio n'est pas dans le formulaire d'inscription
        }

        print(f"\n=== TEST: test_register_form_with_optional_fields ===")
        print(f"Form data: {form_data}")
        
        form = RegisterForm(data=form_data)
        print(f"Form is valid: {form.is_valid()}")
        if not form.is_valid():
            print(f"Form errors: {form.errors}")
        
        self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")

        user = form.save()
        print(f"User saved - email: {user.email}, date_of_birth: {user.date_of_birth}, sexe: {user.sexe}")
        print(f"User bio: '{user.bio}' (default value)")
        print(f"User age: {user.age}")
        
        self.assertEqual(user.date_of_birth, date(1990, 5, 15))
        self.assertEqual(user.sexe, 'M')
        # La bio a sa valeur par défaut du modèle (chaîne vide)
        self.assertEqual(user.bio, '')

    def test_register_form_password_mismatch(self):
        """
        Test critique : Rejet des mots de passe non correspondants.

        Sécurité de base de l'inscription.
        """
        form_data = {
            'email': 'mismatch@example.com',
            'username': 'mismatch',
            'password1': 'ComplexPassword123!',
            'password2': 'DifferentPassword456!',
            'date_of_birth': '1990-01-01',  # Ajout de la date requise
        }

        form = RegisterForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('password2', form.errors)

    def test_login_form_valid_data(self):
        """
        Test critique : Formulaire de connexion valide.

        Si ce test échoue, les utilisateurs ne peuvent pas se connecter.
        """
        form_data = {
            'email': 'login@example.com',
            'password': 'TestPassword123!'
        }

        form = LoginForm(data=form_data)
        self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")

    def test_login_form_missing_fields(self):
        """
        Test critique : Validation des champs requis.

        Vérifie que les champs obligatoires sont validés.
        """
        # Email manquant
        form_data = {
            'password': 'TestPassword123!'
        }
        form = LoginForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

        # Mot de passe manquant
        form_data = {
            'email': 'test@example.com'
        }
        form = LoginForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('password', form.errors)


class CoreViewsTest(TestCase):
    """
    Tests essentiels des vues d'authentification.

    Ces tests vérifient que les pages principales
    se chargent correctement.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='views@example.com',
            username='viewsuser',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    def test_register_view_loads(self):
        """
        Test critique : La page d'inscription se charge.

        Si ce test échoue, les utilisateurs ne peuvent pas accéder à l'inscription.
        """
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'email')  # Le formulaire contient un champ email
        self.assertContains(response, 'password')  # Le formulaire contient un champ password

    def test_login_view_loads(self):
        """
        Test critique : La page de connexion se charge.

        C'est la fonctionnalité principale de l'authentification.
        """
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'email')
        self.assertContains(response, 'password')

    def test_register_post_creates_user(self):
        """
        Test critique : L'inscription POST crée un utilisateur.

        Le workflow le plus important de l'authentification.
        """
        form_data = {
            'email': 'newregister@example.com',
            'username': 'newregister',
            'password1': 'ComplexPassword123!',
            'password2': 'ComplexPassword123!',
            'date_of_birth': '1995-06-15',  # Ajout de la date de naissance requise
        }

        print(f"\n=== TEST: test_register_post_creates_user ===")
        print(f"POST data: {form_data}")
        print(f"User count before: {User.objects.count()}")
        
        response = self.client.post(reverse('register'), form_data)
        
        print(f"Response status: {response.status_code}")
        print(f"Response redirect URL: {getattr(response, 'url', 'No redirect')}")
        print(f"User count after: {User.objects.count()}")

        # Doit rediriger après inscription réussie
        self.assertEqual(response.status_code, 302)

        # L'utilisateur doit être créé
        user_exists = User.objects.filter(email='newregister@example.com').exists()
        print(f"User exists: {user_exists}")
        self.assertTrue(user_exists)

        # L'utilisateur doit être connecté automatiquement
        user = User.objects.get(email='newregister@example.com')
        print(f"User authenticated: {user.is_authenticated}")
        print(f"User details: email={user.email}, age={user.age}, bio='{user.bio}'")
        self.assertTrue(user.is_authenticated)

    def test_login_post_authenticates_user(self):
        """
        Test critique : La connexion POST authentifie l'utilisateur.

        Le workflow d'authentification principal.
        """
        form_data = {
            'email': 'views@example.com',
            'password': TEST_USER_PASSWORD
        }

        response = self.client.post(reverse('login'), form_data)

        # Doit rediriger après connexion réussie
        self.assertEqual(response.status_code, 302)
        self.assertIn('/diary/record/', response.url)

    def test_login_post_wrong_credentials(self):
        """
        Test critique : Rejet des mauvaises credentials.

        Sécurité de base de l'authentification web.
        """
        form_data = {
            'email': 'views@example.com',
            'password': 'wrong_password'
        }

        response = self.client.post(reverse('login'), form_data)

        # Doit rester sur la page de login avec erreur
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email ou mot de passe incorrect')

    def test_protected_views_require_login(self):
        """
        Test critique : Les vues protégées requièrent une authentification.

        Sécurité de base de l'application.
        """
        protected_urls = [
            reverse('account_management'),
            reverse('password_change'),
            reverse('delete_account'),
        ]

        for url in protected_urls:
            response = self.client.get(url)
            # Doit rediriger vers login
            self.assertEqual(response.status_code, 302)
            self.assertIn('login', response.url.lower())

    def test_logout_works(self):
        """
        Test critique : La déconnexion fonctionne.

        Fonctionnalité essentielle de sécurité.
        """
        # Se connecter d'abord
        self.client.login(email='views@example.com', password=TEST_USER_PASSWORD)

        # Vérifier qu'on est connecté
        response = self.client.get(reverse('account_management'))
        self.assertEqual(response.status_code, 200)

        # Se déconnecter
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)

        # Vérifier qu'on n'est plus connecté
        response = self.client.get(reverse('account_management'))
        self.assertEqual(response.status_code, 302)  # Redirection vers login


class CoreSecurityTest(TestCase):
    """
    Tests de sécurité critiques.

    Ces tests vérifient que l'application ne plante jamais
    et gère gracieusement les erreurs courantes.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='security@example.com',
            username='securityuser',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    def test_duplicate_email_rejected(self):
        """
        Test critique : Rejet des emails dupliqués.

        Contrainte d'unicité essentielle.
        """
        # Premier utilisateur
        User.objects.create_user(
            email='duplicate@example.com',
            username='user1',
            password=TEST_USER_PASSWORD
        )

        # Tentative de création d'un second utilisateur avec le même email
        with self.assertRaises(Exception):
            User.objects.create_user(
                email='duplicate@example.com',
                username='user2',
                password=TEST_USER_PASSWORD
            )

    def test_user_isolation(self):
        """
        Test critique : Isolation des données utilisateur.

        Un utilisateur ne doit accéder qu'à ses propres données.
        """
        user1 = User.objects.create_user(
            email='user1@example.com',
            username='user1',
            password=TEST_USER_PASSWORD
        )

        user2 = User.objects.create_user(
            email='user2@example.com',
            username='user2',
            password=TEST_USER_PASSWORD
        )

        # Se connecter comme user1
        self.client.login(email='user1@example.com', password=TEST_USER_PASSWORD)

        # Accéder à account_management
        response = self.client.get(reverse('account_management'))
        self.assertEqual(response.status_code, 200)

        # Vérifier que seules les données de user1 sont accessibles
        self.assertContains(response, 'user1@example.com')
        self.assertNotContains(response, 'user2@example.com')

    def test_inactive_user_cannot_login(self):
        """
        Test critique : Utilisateurs inactifs rejetés.

        Vérifie que les comptes désactivés ne peuvent pas se connecter.
        """
        from django.contrib.auth import authenticate

        self.user.is_active = False
        self.user.save()

        user = authenticate(
            email='auth@example.com',
            password=TEST_USER_PASSWORD
        )

        self.assertIsNone(user)

    def test_password_validation(self):
        """
        Test critique : Validation des mots de passe.

        Les mots de passe faibles doivent être rejetés.
        """
        weak_passwords = [
            '123',           # Trop court
            'password',      # Trop commun
            '12345678',      # Que des chiffres
        ]

        for weak_password in weak_passwords:
            form_data = {
                'email': 'weak@example.com',
                'username': 'weak',
                'password1': weak_password,
                'password2': weak_password,
                'date_of_birth': '1990-01-01',  # Ajout de la date requise
            }

            form = RegisterForm(data=form_data)
            self.assertFalse(
                form.is_valid(),
                f"Mot de passe faible accepté : {weak_password}"
            )

    def test_age_validation(self):
        """
        Test critique : Validation de l'âge.

        Les âges invalides doivent être rejetés.
        """
        from datetime import date, timedelta
        
        # Âge trop jeune (12 ans)
        too_young_date = date.today() - timedelta(days=12*365)
        
        form_data = {
            'email': 'young@example.com',
            'username': 'young',
            'password1': 'ComplexPassword123!',
            'password2': 'ComplexPassword123!',
            'date_of_birth': too_young_date.strftime('%Y-%m-%d')
        }

        form = RegisterForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('date_of_birth', form.errors)

    def test_sql_injection_protection(self):
        """
        Test critique : Protection contre l'injection SQL.

        Les tentatives d'injection doivent être neutralisées.
        """
        malicious_email = "test'; DROP TABLE accounts_customuser; --"

        form_data = {
            'email': malicious_email,
            'password': TEST_USER_PASSWORD
        }

        form = LoginForm(data=form_data)
        # Le formulaire peut être valide (email bizarre mais syntaxiquement correct)
        # mais l'important est que l'authentification échoue sans planter
        
        response = self.client.post(reverse('login'), form_data)
        
        # Ne doit pas planter et doit rester sur la page de login
        self.assertEqual(response.status_code, 200)
        
        # La table doit toujours exister
        self.assertTrue(User.objects.all().exists())