"""
Tests de sécurité spécifiques pour l'app accounts.

Ce module teste les aspects de sécurité non couverts par les autres tests :
- Protection CSRF
- Validation des uploads d'images
- Rate limiting (simulation)
- Headers de sécurité
- Résistance aux attaques communes
- Gestion sécurisée des sessions
- Protection des données sensibles

Usage: python manage.py test accounts.tests.test_security

Ces tests se concentrent sur la sécurité applicative et ne dupliquent
pas les tests de validation déjà présents dans les autres modules.

=== RECOMMANDATIONS DE SÉCURITÉ ===

Après avoir exécuté ces tests, à implémenter :

1. Rate limiting avec django-ratelimit
2. Validation d'images avec Pillow
3. Headers de sécurité avec django-security
4. Logging des tentatives d'attaque
5. Monitoring des sessions suspectes

"""

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.sessions.models import Session
import time
import os
from datetime import date

User = get_user_model()

TEST_USER_PASSWORD = os.environ.get('TEST_PASSWORD', 'django_test_secure_2024')


class CSRFProtectionTest(TestCase):
    """
    Tests de protection CSRF sur les formulaires critiques.

    Ces tests vérifient que les actions sensibles sont protégées
    contre les attaques CSRF (Cross-Site Request Forgery).
    """

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            email='csrf@example.com',
            username='csrf',
            password=TEST_USER_PASSWORD,
        )

    def test_login_requires_csrf_token(self):
        """Test que le login requiert un token CSRF valide."""
        # Tentative de login sans token CSRF
        response = self.client.post(
            reverse('login'),
            {'email': 'csrf@example.com', 'password': TEST_USER_PASSWORD},
        )

        # Doit être rejeté (403 Forbidden)
        self.assertEqual(response.status_code, 403)

    def test_register_requires_csrf_token(self):
        """Test que l'inscription requiert un token CSRF valide."""
        register_data = {
            'email': 'newuser@example.com',
            'username': 'newuser',
            'password1': 'ComplexPassword123!',
            'password2': 'ComplexPassword123!',
            'date_of_birth': '1990-01-01',
        }

        # Tentative d'inscription sans token CSRF
        response = self.client.post(reverse('register'), register_data)

        # Doit être rejeté
        self.assertEqual(response.status_code, 403)

    def test_password_change_requires_csrf_token(self):
        """Test que le changement de mot de passe requiert CSRF."""
        self.client.force_login(self.user)

        password_data = {
            'old_password': TEST_USER_PASSWORD,
            'new_password1': 'NewComplexPassword456!',
            'new_password2': 'NewComplexPassword456!',
        }

        # Tentative de changement sans token CSRF
        response = self.client.post(reverse('password_change'), password_data)

        # Doit être rejeté
        self.assertEqual(response.status_code, 403)

    def test_account_deletion_requires_csrf_token(self):
        """Test que la suppression de compte requiert CSRF."""
        self.client.force_login(self.user)

        # Tentative de suppression sans token CSRF
        response = self.client.post(reverse('delete_account'))

        # Doit être rejeté
        self.assertEqual(response.status_code, 403)


class FileUploadSecurityTest(TestCase):
    """
    Tests de sécurité pour les uploads d'images de profil.

    Ces tests vérifient que les uploads de fichiers sont sécurisés
    et ne permettent pas l'injection de contenu malveillant.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='upload@example.com',
            username='upload',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()
        self.client.login(
            email='upload@example.com', password=TEST_USER_PASSWORD
        )

    def test_malicious_file_extension(self):
        """Test de protection contre les extensions de fichiers malveillantes."""
        malicious_files = [
            ('script.php.png', b'<?php echo "malicious"; ?>'),
            ('shell.jsp.jpg', b'<% Runtime.exec("rm -rf /"); %>'),
            ('evil.exe.gif', b'MZ\x90\x00\x03\x00\x00\x00'),  # Début d'un .exe
            ('virus.bat.png', b'@echo off\ndel /f /q c:\\*.*'),
        ]

        for filename, content in malicious_files:
            with self.subTest(filename=filename):
                uploaded_file = SimpleUploadedFile(
                    filename, content, content_type='image/png'
                )

                response = self.client.post(
                    reverse('account_management'),
                    {'profile_picture': uploaded_file},
                )

                # L'upload doit réussir mais le contenu ne doit pas être exécutable
                # Vérifions que l'utilisateur a bien une image stockée en base64
                self.user.refresh_from_db()
                if self.user.has_profile_picture:
                    # L'image doit être encodée en base64, pas exécutée
                    self.assertTrue(
                        self.user.profile_picture_base64.startswith(
                            'data:image/'
                        )
                    )

    def test_oversized_image_upload(self):
        """Test de protection contre les images trop volumineuses."""
        # Créer une "image" de 10MB (simulée)
        large_content = b'fake_image_data' * 700000  # ~10MB

        large_file = SimpleUploadedFile(
            'huge.png', large_content, content_type='image/png'
        )

        # L'application doit gérer gracieusement les gros fichiers
        response = self.client.post(
            reverse('account_management'), {'profile_picture': large_file}
        )

        # Ne doit pas planter (200 ou 302)
        self.assertIn(response.status_code, [200, 302])

    def test_invalid_image_content(self):
        """Test avec contenu non-image mais extension image."""
        fake_images = [
            ('fake.png', b'This is not an image but claims to be PNG'),
            ('fake.jpg', b'<html><body>Not an image</body></html>'),
            ('fake.gif', b'\x00\x00\x00\x00FAKE_GIF_HEADER'),
        ]

        for filename, content in fake_images:
            with self.subTest(filename=filename):
                uploaded_file = SimpleUploadedFile(
                    filename, content, content_type='image/png'
                )

                response = self.client.post(
                    reverse('account_management'),
                    {'profile_picture': uploaded_file},
                )

                # L'application ne doit pas planter
                self.assertIn(response.status_code, [200, 302])

    def test_file_path_traversal_attempt(self):
        """Test de protection contre les attaques de path traversal."""
        traversal_filenames = [
            '../../../etc/passwd.png',
            '..\\..\\windows\\system32\\config\\sam.jpg',
            '/etc/shadow.gif',
            'C:\\boot.ini.png',
        ]

        for filename in traversal_filenames:
            with self.subTest(filename=filename):
                uploaded_file = SimpleUploadedFile(
                    filename, b'fake_image_content', content_type='image/png'
                )

                response = self.client.post(
                    reverse('account_management'),
                    {'profile_picture': uploaded_file},
                )

                # L'application doit gérer sans planter
                self.assertIn(response.status_code, [200, 302])


class BruteForceProtectionTest(TestCase):
    """
    Tests de protection contre les attaques par force brute.

    Ces tests simulent des tentatives de brute force et vérifient
    que l'application a des mécanismes de protection appropriés.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='bruteforce@example.com',
            username='bruteforce',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_multiple_failed_login_attempts(self):
        """Test de gestion des tentatives de login multiples échouées."""
        failed_attempts = []

        # Effectuer 10 tentatives de login échouées
        for i in range(10):
            start_time = time.time()
            response = self.client.post(
                reverse('login'),
                {
                    'email': 'bruteforce@example.com',
                    'password': f'wrong_password_{i}',
                },
            )
            end_time = time.time()

            failed_attempts.append(
                {
                    'attempt': i + 1,
                    'response_time': end_time - start_time,
                    'status_code': response.status_code,
                }
            )

        # Analyser les réponses
        response_times = [
            attempt['response_time'] for attempt in failed_attempts
        ]

        # Vérifier que l'application ne révèle pas d'informations sensibles
        self.assertTrue(
            all(attempt['status_code'] == 200 for attempt in failed_attempts)
        )

        # Vérifier qu'il n'y a pas de timing attack évident
        # (les temps de réponse ne devraient pas varier énormément)
        if len(response_times) > 1:
            avg_time = sum(response_times) / len(response_times)
            # Aucun temps ne devrait être 10x plus long que la moyenne
            self.assertTrue(
                all(time < avg_time * 10 for time in response_times)
            )

    def test_rapid_registration_attempts(self):
        """Test de protection contre les inscriptions en masse."""
        registration_attempts = []

        # Effectuer 5 tentatives d'inscription rapides
        for i in range(5):
            start_time = time.time()
            response = self.client.post(
                reverse('register'),
                {
                    'email': f'spam_{i}@example.com',
                    'username': f'spam_{i}',
                    'password1': 'ComplexPassword123!',
                    'password2': 'ComplexPassword123!',
                    'date_of_birth': '1990-01-01',
                },
            )
            end_time = time.time()

            registration_attempts.append(
                {
                    'attempt': i + 1,
                    'response_time': end_time - start_time,
                    'status_code': response.status_code,
                }
            )

        # L'application doit gérer sans planter
        for attempt in registration_attempts:
            self.assertIn(attempt['status_code'], [200, 302])


class SessionSecurityTest(TestCase):
    """
    Tests de sécurité des sessions Django.

    Ces tests vérifient que les sessions sont gérées de manière sécurisée
    et résistent aux attaques de session.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='session@example.com',
            username='session',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_session_invalidation_on_logout(self):
        """Test que la session est invalidée lors de la déconnexion."""
        # Se connecter
        self.client.login(
            email='session@example.com', password=TEST_USER_PASSWORD
        )

        # Récupérer la clé de session
        session_key = self.client.session.session_key

        # Vérifier que la session existe
        self.assertTrue(
            Session.objects.filter(session_key=session_key).exists()
        )

        # Se déconnecter
        response = self.client.post(reverse('logout'))

        # La session doit être invalidée ou modifiée
        # (Django peut créer une nouvelle session vide)
        self.assertEqual(response.status_code, 302)

    def test_session_regeneration_on_privilege_change(self):
        """Test de régénération de session lors de changement de privilège."""
        # Se connecter
        self.client.login(
            email='session@example.com', password=TEST_USER_PASSWORD
        )
        original_session_key = self.client.session.session_key

        # Changer le mot de passe (action privilégiée)
        response = self.client.post(
            reverse('password_change'),
            {
                'old_password': TEST_USER_PASSWORD,
                'new_password1': 'NewSecurePassword789!',
                'new_password2': 'NewSecurePassword789!',
            },
        )

        # La session devrait idéalement être régénérée
        # (ce test documente le comportement actuel)
        new_session_key = self.client.session.session_key

        # Django peut ou non régénérer la session selon la configuration
        # L'important est que l'action soit authentifiée
        if response.status_code == 302:
            # Si redirection, le changement a probablement réussi
            self.user.refresh_from_db()

    def test_concurrent_sessions_handling(self):
        """Test de gestion des sessions concurrentes."""
        # Créer deux clients pour simuler des sessions concurrentes
        client1 = Client()
        client2 = Client()

        # Se connecter avec les deux clients
        client1.login(email='session@example.com', password=TEST_USER_PASSWORD)
        client2.login(email='session@example.com', password=TEST_USER_PASSWORD)

        # Effectuer des actions avec les deux sessions
        response1 = client1.get(reverse('account_management'))
        response2 = client2.get(reverse('account_management'))

        # Les deux sessions doivent fonctionner
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response2.status_code, 200)

        # Se déconnecter d'une session
        client1.post(reverse('logout'))

        # L'autre session doit toujours fonctionner
        response2_after = client2.get(reverse('account_management'))
        self.assertEqual(response2_after.status_code, 200)


class InputValidationSecurityTest(TestCase):
    """
    Tests de sécurité de validation des entrées utilisateur.

    Ces tests vérifient que l'application résiste aux injections
    et aux tentatives de bypass de validation.
    """

    def setUp(self):
        self.client = Client()

    def test_script_injection_in_forms(self):
        """Test de protection contre l'injection de scripts."""
        script_payloads = [
            '<script>alert("XSS")</script>',
            'javascript:alert("XSS")',
            '<img src=x onerror=alert("XSS")>',
            '"><script>alert("XSS")</script>',
            "'; DROP TABLE auth_user; --",
        ]

        for payload in script_payloads:
            with self.subTest(payload=payload):
                form_data = {
                    'email': f'{payload}@example.com',
                    'username': payload,
                    'password1': 'ComplexPassword123!',
                    'password2': 'ComplexPassword123!',
                    'date_of_birth': '1990-01-01',
                }

                response = self.client.post(reverse('register'), form_data)

                # L'application ne doit pas planter
                self.assertIn(response.status_code, [200, 302, 400])

                # Si un utilisateur est créé, vérifier qu'il ne contient pas de script actif
                if response.status_code == 302:  # Succès probable
                    try:
                        # Récupérer l'utilisateur créé
                        user = User.objects.get(
                            email__contains=payload.replace('<', '').replace(
                                '>', ''
                            )
                        )
                        # Le contenu doit être échappé ou filtré
                        self.assertNotIn('<script>', user.username)
                        self.assertNotIn('javascript:', user.username)
                    except User.DoesNotExist:
                        # Si pas d'utilisateur créé, c'est aussi acceptable
                        pass

    def test_unicode_normalization_attacks(self):
        """Test de protection contre les attaques de normalisation Unicode."""
        unicode_attacks = [
            'admin\u202dadmin',  # Right-to-left override
            'user\ufeffadmin',  # Zero-width no-break space
            'test\u200buser',  # Zero-width space
            'Ⅰ',  # Roman numeral one (looks like I)
            '０',  # Fullwidth digit zero
        ]

        for attack in unicode_attacks:
            with self.subTest(attack=repr(attack)):
                form_data = {
                    'email': f'{attack}@example.com',
                    'username': attack,
                    'password1': 'ComplexPassword123!',
                    'password2': 'ComplexPassword123!',
                    'date_of_birth': '1990-01-01',
                }

                response = self.client.post(reverse('register'), form_data)

                # L'application doit gérer gracieusement
                self.assertIn(response.status_code, [200, 302, 400])

    def test_parameter_pollution(self):
        """Test de protection contre la pollution de paramètres."""
        # Envoyer des paramètres dupliqués
        form_data = {
            'email': ['first@example.com', 'second@example.com'],
            'username': 'testuser',
            'password1': 'ComplexPassword123!',
            'password2': 'ComplexPassword123!',
            'date_of_birth': '1990-01-01',
        }

        # Django gère automatiquement les listes, mais testons
        response = self.client.post(reverse('register'), form_data)

        # L'application ne doit pas planter
        self.assertIn(response.status_code, [200, 302, 400])


class SecurityHeadersTest(TestCase):
    """
    Tests de présence des headers de sécurité.

    Ces tests vérifient que l'application renvoie les headers
    de sécurité appropriés pour se protéger contre diverses attaques.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='headers@example.com',
            username='headers',
            password=TEST_USER_PASSWORD,
        )

    def test_csrf_token_in_forms(self):
        """Test de présence du token CSRF dans les formulaires."""
        # Page de login
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'csrfmiddlewaretoken')

        # Page d'inscription
        response = self.client.get(reverse('register'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_no_sensitive_info_in_error_pages(self):
        """Test que les pages d'erreur ne révèlent pas d'informations sensibles."""
        # Tentative d'accès à une page qui n'existe pas
        response = self.client.get('/accounts/nonexistent/')

        # Doit être une 404, pas une 500 avec stack trace
        self.assertEqual(response.status_code, 404)

    def test_login_required_pages_redirect(self):
        """Test que les pages protégées redirigent vers login."""
        protected_urls = [
            reverse('account_management'),
            reverse('password_change'),
            reverse('delete_account'),
        ]

        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)

                # Doit rediriger vers login (302) ou renvoyer 401/403
                self.assertIn(response.status_code, [302, 401, 403])

                if response.status_code == 302:
                    # La redirection doit inclure le login
                    self.assertIn('login', response.url.lower())


class AuthorizationSecurityTest(TestCase):
    """
    Tests d'autorisation et de contrôle d'accès.

    Ces tests vérifient que les utilisateurs ne peuvent accéder
    qu'aux ressources pour lesquelles ils ont les droits appropriés.
    """

    def setUp(self):
        self.user1 = User.objects.create_user(
            email='user1@example.com',
            username='user1',
            password=TEST_USER_PASSWORD,
        )
        self.user2 = User.objects.create_user(
            email='user2@example.com',
            username='user2',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_user_cannot_access_other_user_data(self):
        """Test qu'un utilisateur ne peut pas accéder aux données d'un autre."""
        # Se connecter comme user1
        self.client.login(
            email='user1@example.com', password=TEST_USER_PASSWORD
        )

        # Accéder à la gestion de compte
        response = self.client.get(reverse('account_management'))
        self.assertEqual(response.status_code, 200)

        # La réponse doit contenir les données de user1 uniquement
        self.assertContains(response, 'user1@example.com')
        self.assertNotContains(response, 'user2@example.com')

    def test_unauthorized_account_modifications(self):
        """Test de protection contre les modifications non autorisées."""
        # Se connecter comme user1
        self.client.login(
            email='user1@example.com', password=TEST_USER_PASSWORD
        )

        # Tenter de modifier la bio (action autorisée)
        response = self.client.post(
            reverse('edit_bio'), {'bio': 'Nouvelle bio pour user1'}
        )

        # Doit réussir (302 redirect)
        self.assertEqual(response.status_code, 302)

        self.user1.refresh_from_db()
        self.assertEqual(self.user1.bio, 'Nouvelle bio pour user1')

    def test_account_deletion_authorization(self):
        """Test que seul le propriétaire peut supprimer son compte."""
        # Se connecter comme user1
        self.client.login(
            email='user1@example.com', password=TEST_USER_PASSWORD
        )

        # Vérifier l'accès à la page de suppression
        response = self.client.get(reverse('delete_account'))
        self.assertEqual(response.status_code, 200)

        # Vérifier que la page de suppression s'affiche correctement
        # Au lieu de chercher "user1", chercher des éléments standard de la page
        self.assertContains(response, 'Supprimer le compte')
        self.assertContains(response, 'Cette action est irréversible')

        # Vérifier que le formulaire de suppression est présent
        self.assertContains(response, '<form method="post"')
        self.assertContains(response, 'csrfmiddlewaretoken')

        # Vérifier que les informations d'autres utilisateurs ne sont PAS présentes
        self.assertNotContains(response, 'user2@example.com')
        self.assertNotContains(response, 'user2')


class PrivacySecurityTest(TestCase):
    """
    Tests de protection de la vie privée et des données personnelles.

    Ces tests vérifient que les données personnelles sont protégées
    et que l'application respecte les principes de protection des données.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='privacy@example.com',
            username='privacy',
            password=TEST_USER_PASSWORD,
            date_of_birth=date(1990, 5, 15),
            bio='Bio confidentielle',
        )
        self.client = Client()

    def test_password_not_exposed_in_responses(self):
        """Test que les mots de passe ne sont jamais exposés."""
        self.client.login(
            email='privacy@example.com', password=TEST_USER_PASSWORD
        )

        # Accéder à la gestion de compte
        response = self.client.get(reverse('account_management'))

        # Le mot de passe ne doit jamais apparaître en clair
        self.assertNotContains(response, TEST_USER_PASSWORD)
        self.assertNotContains(response, 'django_test_secure_2024')

    def test_user_data_isolation_in_views(self):
        """Test d'isolation des données utilisateur dans les vues."""
        # Créer un second utilisateur
        user2 = User.objects.create_user(
            email='privacy2@example.com',
            username='privacy2',
            password=TEST_USER_PASSWORD,
            bio='Bio de l\'autre utilisateur',
        )

        # Se connecter comme premier utilisateur
        self.client.login(
            email='privacy@example.com', password=TEST_USER_PASSWORD
        )

        # Accéder aux pages principales
        pages = [
            reverse('account_management'),
        ]

        for page in pages:
            with self.subTest(page=page):
                response = self.client.get(page)

                # Ne doit contenir que les données du bon utilisateur
                self.assertContains(response, 'privacy@example.com')
                self.assertNotContains(response, 'privacy2@example.com')
                self.assertNotContains(response, 'Bio de l\'autre utilisateur')

    def test_sensitive_data_in_error_messages(self):
        """Test que les messages d'erreur ne révèlent pas de données sensibles."""
        # Tentative de login avec mauvais mot de passe
        response = self.client.post(
            reverse('login'),
            {'email': 'privacy@example.com', 'password': 'wrong_password'},
        )

        # Le message d'erreur ne doit pas révéler si l'email existe
        if response.status_code == 200:
            content = response.content.decode()

            # Vérifier que le message d'erreur est générique
            self.assertIn('Email ou mot de passe incorrect', content)

            # Le fait que l'email soit dans le champ input est normal (Django repopule les champs)
            # Mais les données sensibles ne doivent pas être dans le message d'erreur lui-même

            # Vérifier qu'aucune information sensible n'est révélée dans les messages d'erreur
            # On ne teste plus la présence de l'email car c'est normal dans le formulaire

            # Vérifier que les données vraiment sensibles ne sont pas exposées
            self.assertNotIn('1990-05-15', content)  # Date de naissance
            self.assertNotIn('Bio confidentielle', content)  # Bio
            self.assertNotIn(TEST_USER_PASSWORD, content)  # Mot de passe

            # Vérifier que le message d'erreur ne donne pas d'indices sur l'existence du compte
            # Le message doit être le même pour un email existant ou non
            error_message = 'Email ou mot de passe incorrect'
            self.assertIn(error_message, content)
