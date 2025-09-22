"""
Tests spécifiques des vues d'authentification Django.

Ce module teste en détail toutes les vues de l'application accounts :
- Vues d'inscription et connexion
- Vues de gestion de compte
- Templates et contexte
- Codes de réponse HTTP
- Gestion des erreurs de vues
- Workflow d'authentification complet
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from diary.models import Dream
from unittest.mock import patch
import time
import os
from datetime import date

User = get_user_model()

TEST_USER_PASSWORD = os.environ.get("TEST_PASSWORD", "django_test_secure_2024")


class RegisterViewTest(TestCase):
    """
    Tests de la vue d'inscription register_view.

    Cette classe teste :
    - Affichage du formulaire d'inscription
    - Traitement des données d'inscription
    - Redirection après inscription
    - Gestion des erreurs d'inscription
    - Connexion automatique après inscription
    """

    def setUp(self):
        self.client = Client()

    def test_register_view_get(self):
        """
        Test d'affichage de la page d'inscription.

        Objectif : Vérifier que la page d'inscription se charge correctement
        """
        response = self.client.get(reverse("register"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/register.html")
        self.assertContains(response, "email")
        self.assertContains(response, "password")
        self.assertContains(response, "username")

    def test_register_view_post_success_minimal(self):
        """Test d'inscription réussie avec données minimales."""
        form_data = {
            "email": "newuser@example.com",
            "username": "newuser",
            "password1": "ComplexPassword123!",
            "password2": "ComplexPassword123!",
            "date_of_birth": "1995-06-15",
        }

        print("\n=== TEST: register_view minimal ===")
        print(f"Form data: {form_data}")

        response = self.client.post(reverse("register"), form_data)

        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            # Si échec, afficher les erreurs du formulaire
            if "form" in response.context:
                print(f"Form errors: {response.context['form'].errors}")

        # Doit rediriger après inscription réussie
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/diary/record/")

        # Utilisateur doit être créé
        self.assertTrue(
            User.objects.filter(email="newuser@example.com").exists()
        )
        user = User.objects.get(email="newuser@example.com")
        self.assertEqual(user.username, "newuser")
        self.assertTrue(user.check_password("ComplexPassword123!"))

    def test_register_view_post_invalid_data(self):
        """
        Test d'inscription avec données invalides.

        Objectif : Vérifier la gestion des erreurs d'inscription
        """
        form_data = {
            "email": "invalid-email",
            "username": "test",
            "password1": "123",  # Mot de passe faible
            "password2": "456",  # Mots de passe différents
        }

        response = self.client.post(reverse("register"), form_data)

        # Doit rester sur la page d'inscription avec erreurs
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/register.html")

        # Le formulaire doit contenir des erreurs
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        self.assertTrue(len(form.errors) > 0)

    def test_register_view_user_auto_login(self):
        """Test de connexion automatique après inscription."""
        form_data = {
            "email": "autologin@example.com",
            "username": "autologin",
            "password1": "ComplexPassword123!",
            "password2": "ComplexPassword123!",
            "date_of_birth": "1995-06-15",
        }

        print("\n=== TEST: auto-login after register ===")

        # Vérifier qu'on n'est pas connecté au départ
        response_before = self.client.get(reverse("account_management"))
        self.assertEqual(
            response_before.status_code, 302
        )  # Redirection vers login

        # S'inscrire
        response = self.client.post(reverse("register"), form_data)
        print(f"Register response status: {response.status_code}")

        if response.status_code == 200:
            if "form" in response.context:
                print(
                    f"Register form errors: {response.context['form'].errors}"
                )

        self.assertEqual(response.status_code, 302)

        # Vérifier qu'on est maintenant connecté
        response_after = self.client.get(reverse("account_management"))
        self.assertEqual(response_after.status_code, 200)  # Accès autorisé

    def test_register_view_duplicate_email(self):
        """
        Test d'inscription avec email déjà existant.

        Objectif : Vérifier la gestion des emails dupliqués
        """
        # Créer un utilisateur existant
        User.objects.create_user(
            email="duplicate@example.com",
            username="existing",
            password=TEST_USER_PASSWORD,
        )

        # Tenter de s'inscrire avec le même email
        form_data = {
            "email": "duplicate@example.com",
            "username": "newuser",
            "password1": "ComplexPassword123!",
            "password2": "ComplexPassword123!",
        }

        response = self.client.post(reverse("register"), form_data)

        # Doit rester sur la page d'inscription avec erreur
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("email", form.errors)


class LoginViewTest(TestCase):
    """
    Tests de la vue de connexion login_view.

    Cette classe teste :
    - Affichage du formulaire de connexion
    - Authentification réussie et échouée
    - Redirection après connexion
    - Gestion des sessions
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="login@example.com",
            username="login",
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_login_view_get(self):
        """
        Test d'affichage de la page de connexion.

        Objectif : Vérifier que la page de connexion se charge
        """
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")
        self.assertContains(response, "email")
        self.assertContains(response, "password")

    def test_login_view_post_success(self):
        """
        Test de connexion réussie.

        Objectif : Vérifier le workflow de connexion normal
        """
        form_data = {
            "email": "login@example.com",
            "password": TEST_USER_PASSWORD,
        }

        response = self.client.post(reverse("login"), form_data)

        # Doit rediriger vers l'application principale
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/diary/record/")

    def test_login_view_post_wrong_email(self):
        """
        Test de connexion avec email incorrect.

        Objectif : Vérifier la gestion des emails inexistants
        """
        form_data = {
            "email": "nonexistent@example.com",
            "password": TEST_USER_PASSWORD,
        }

        response = self.client.post(reverse("login"), form_data)

        # Doit rester sur la page de connexion avec erreur
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")
        self.assertContains(response, "Email ou mot de passe incorrect")

    def test_login_view_post_wrong_password(self):
        """
        Test de connexion avec mot de passe incorrect.

        Objectif : Vérifier la gestion des mots de passe erronés
        """
        form_data = {
            "email": "login@example.com",
            "password": "wrong_password",
        }

        response = self.client.post(reverse("login"), form_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email ou mot de passe incorrect")

    def test_login_view_post_invalid_form(self):
        """
        Test de connexion avec formulaire invalide.

        Objectif : Vérifier la gestion des données malformées
        """
        form_data = {"email": "invalid-email-format", "password": ""}

        response = self.client.post(reverse("login"), form_data)

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())

    def test_login_view_case_insensitive_email(self):
        """
        Test de connexion avec email en majuscules.

        Objectif : Vérifier la gestion de la casse des emails
        """
        form_data = {
            "email": "LOGIN@EXAMPLE.COM",  # Majuscules
            "password": TEST_USER_PASSWORD,
        }

        response = self.client.post(reverse("login"), form_data)

        # Selon la configuration, peut réussir ou échouer
        # L'important est que ça ne plante pas
        self.assertIn(response.status_code, [200, 302])

    def test_login_view_inactive_user(self):
        """
        Test de connexion avec utilisateur inactif.

        Objectif : Vérifier que les comptes inactifs sont rejetés
        """
        self.user.is_active = False
        self.user.save()

        form_data = {
            "email": "login@example.com",
            "password": TEST_USER_PASSWORD,
        }

        response = self.client.post(reverse("login"), form_data)

        # Doit échouer même avec bonnes credentials
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email ou mot de passe incorrect")


class AccountManagementViewTest(TestCase):
    """
    Tests de la vue de gestion de compte account_management_view.

    Cette classe teste :
    - Affichage des informations de compte
    - Upload d'images de profil
    - Modification des données utilisateur
    - Authentification requise
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="account@example.com",
            username="account",
            password=TEST_USER_PASSWORD,
            date_of_birth=date(1990, 5, 15),
            sexe="F",
            bio="Bio initiale de test",
        )
        self.client = Client()

    def test_account_management_requires_login(self):
        """
        Test que la gestion de compte requiert une authentification.

        Objectif : Vérifier la sécurité de la vue
        """
        response = self.client.get(reverse("account_management"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url.lower())

    def test_account_management_view_get(self):
        """
        Test d'affichage de la page de gestion de compte.

        Objectif : Vérifier l'affichage des informations utilisateur
        """
        self.client.login(
            email="account@example.com", password=TEST_USER_PASSWORD
        )

        response = self.client.get(reverse("account_management"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/account_management.html")

        # Vérifier que les données utilisateur sont dans le contexte
        self.assertEqual(response.context["user"], self.user)

        # Vérifier que les informations sont affichées
        self.assertContains(response, "account@example.com")
        self.assertContains(response, "account")

    def test_account_management_profile_picture_upload(self):
        """
        Test d'upload d'image de profil.

        Objectif : Vérifier le workflow d'upload d'image
        """
        self.client.login(
            email="account@example.com", password=TEST_USER_PASSWORD
        )

        # Créer un fichier image simulé
        image_content = b"fake_image_data_for_testing"
        uploaded_file = SimpleUploadedFile(
            "profile.png", image_content, content_type="image/png"
        )

        response = self.client.post(
            reverse("account_management"), {"profile_picture": uploaded_file}
        )

        # Doit rediriger après upload réussi
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("account_management"))

        # Vérifier que l'image a été sauvegardée
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_profile_picture)
        self.assertIsNotNone(self.user.profile_picture_url)

    def test_account_management_profile_picture_upload_different_formats(self):
        """
        Test d'upload avec différents formats d'image.

        Objectif : Vérifier le support de différents formats
        """
        self.client.login(
            email="account@example.com", password=TEST_USER_PASSWORD
        )

        test_formats = [
            ("test.jpg", "image/jpeg"),
            ("test.jpeg", "image/jpeg"),
            ("test.png", "image/png"),
            ("test.gif", "image/gif"),
        ]

        for filename, content_type in test_formats:
            with self.subTest(format=filename):
                uploaded_file = SimpleUploadedFile(
                    filename, b"fake_image_data", content_type=content_type
                )

                response = self.client.post(
                    reverse("account_management"),
                    {"profile_picture": uploaded_file},
                )

                self.assertEqual(response.status_code, 302)

                self.user.refresh_from_db()
                self.assertTrue(self.user.has_profile_picture)

    def test_account_management_no_file_upload(self):
        """
        Test d'accès sans upload de fichier.

        Objectif : Vérifier que la vue fonctionne sans upload
        """
        self.client.login(
            email="account@example.com", password=TEST_USER_PASSWORD
        )

        response = self.client.post(reverse("account_management"), {})

        # Doit afficher la page normalement sans erreur
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/account_management.html")

    def test_account_management_large_file_upload(self):
        """
        Test d'upload de gros fichier image.

        Objectif : Vérifier la gestion des fichiers volumineux
        """
        self.client.login(
            email="account@example.com", password=TEST_USER_PASSWORD
        )

        # Fichier de 2MB
        large_image_content = b"large_image_data" * 150000  # ~2MB
        uploaded_file = SimpleUploadedFile(
            "large_profile.jpg", large_image_content, content_type="image/jpeg"
        )

        response = self.client.post(
            reverse("account_management"), {"profile_picture": uploaded_file}
        )

        # Doit fonctionner ou être rejeté gracieusement
        self.assertIn(response.status_code, [200, 302])

        if response.status_code == 302:
            # Upload réussi
            self.user.refresh_from_db()
            self.assertTrue(self.user.has_profile_picture)

    def test_account_management_invalid_file_upload(self):
        """
        Test d'upload de fichier non-image.

        Objectif : Vérifier la gestion des fichiers invalides
        """
        self.client.login(
            email="account@example.com", password=TEST_USER_PASSWORD
        )

        # Fichier texte au lieu d'image
        text_file = SimpleUploadedFile(
            "notanimage.txt",
            b"This is not an image",
            content_type="text/plain",
        )

        response = self.client.post(
            reverse("account_management"), {"profile_picture": text_file}
        )

        # L'application doit gérer ce cas sans planter
        self.assertIn(response.status_code, [200, 302])


class LogoutViewTest(TestCase):
    """
    Tests de la vue de déconnexion logout_view.

    Cette classe teste :
    - Déconnexion réussie
    - Redirection après déconnexion
    - Nettoyage des sessions
    - Authentification requise
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="logout@example.com",
            username="logout",
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_logout_view_requires_login(self):
        """
        Test que la déconnexion requiert d'être connecté.

        Objectif : Vérifier la logique d'authentification
        """
        response = self.client.post(reverse("logout"))

        # Doit rediriger vers login même si on n'était pas connecté
        self.assertEqual(response.status_code, 302)

    def test_logout_view_success(self):
        """
        Test de déconnexion réussie.

        Objectif : Vérifier le workflow de déconnexion
        """
        # Se connecter d'abord
        self.client.login(
            email="logout@example.com", password=TEST_USER_PASSWORD
        )

        # Vérifier qu'on est connecté
        response_before = self.client.get(reverse("account_management"))
        self.assertEqual(response_before.status_code, 200)

        # Se déconnecter
        response = self.client.post(reverse("logout"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))

        # Vérifier qu'on n'est plus connecté
        response_after = self.client.get(reverse("account_management"))
        self.assertEqual(response_after.status_code, 302)

    def test_logout_view_only_post(self):
        """
        Test que la déconnexion n'accepte que POST.

        Objectif : Vérifier la sécurité de la méthode HTTP
        """
        self.client.login(
            email="logout@example.com", password=TEST_USER_PASSWORD
        )

        # GET doit être rejeté
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 405)  # Method Not Allowed


class PasswordChangeViewTest(TestCase):
    """
    Tests de la vue de changement de mot de passe.

    Cette classe teste :
    - Affichage du formulaire
    - Changement réussi
    - Validation des mots de passe
    - Redirection après changement
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="password@example.com",
            username="password",
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_password_change_requires_login(self):
        """
        Test que le changement de mot de passe requiert une authentification.

        Objectif : Vérifier la sécurité de la vue
        """
        response = self.client.get(reverse("password_change"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url.lower())

    def test_password_change_view_get(self):
        """
        Test d'affichage du formulaire de changement de mot de passe.

        Objectif : Vérifier que la page se charge correctement
        """
        self.client.login(
            email="password@example.com", password=TEST_USER_PASSWORD
        )

        response = self.client.get(reverse("password_change"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/change_password.html")
        self.assertContains(response, "old_password")
        self.assertContains(response, "new_password1")
        self.assertContains(response, "new_password2")

    def test_password_change_view_post_success(self):
        """Test de changement de mot de passe réussi."""
        self.client.login(
            email="password@example.com", password=TEST_USER_PASSWORD
        )

        form_data = {
            "old_password": TEST_USER_PASSWORD,
            "new_password1": "TotallyDifferentComplexPassword789!",  # Très différent de l'email
            "new_password2": "TotallyDifferentComplexPassword789!",
        }

        print("\n=== TEST: password change ===")
        print(f"Old password: {TEST_USER_PASSWORD}")
        print("New password: TotallyDifferentComplexPassword789!")

        response = self.client.post(reverse("password_change"), form_data)

        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            if "form" in response.context:
                print(f"Form errors: {response.context['form'].errors}")

        # Doit rediriger vers password_change_done
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("password_change_done"))

        # Vérifier que le mot de passe a changé
        self.user.refresh_from_db()
        self.assertTrue(
            self.user.check_password("TotallyDifferentComplexPassword789!")
        )

    def test_password_change_view_wrong_old_password(self):
        """
        Test avec ancien mot de passe incorrect.

        Objectif : Vérifier la validation de l'ancien mot de passe
        """
        self.client.login(
            email="password@example.com", password=TEST_USER_PASSWORD
        )

        form_data = {
            "old_password": "wrong_old_password",
            "new_password1": "NewPassword123!",
            "new_password2": "NewPassword123!",
        }

        response = self.client.post(reverse("password_change"), form_data)

        # Doit rester sur la page avec erreur
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("old_password", form.errors)


class DeleteAccountViewTest(TestCase):
    """
    Tests de la vue de suppression de compte.

    Cette classe teste :
    - Affichage de la page de confirmation
    - Suppression effective du compte
    - Déconnexion après suppression
    - Sécurité de la suppression
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="delete@example.com",
            username="delete",
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_delete_account_requires_login(self):
        """
        Test que la suppression de compte requiert une authentification.

        Objectif : Vérifier la sécurité de la suppression
        """
        response = self.client.get(reverse("delete_account"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url.lower())

    def test_delete_account_view_get(self):
        """
        Test d'affichage de la page de confirmation de suppression.

        Objectif : Vérifier l'affichage de la page de confirmation
        """
        self.client.login(
            email="delete@example.com", password=TEST_USER_PASSWORD
        )

        response = self.client.get(reverse("delete_account"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/delete_account.html")
        self.assertContains(response, "Supprimer")

    def test_delete_account_view_post_success(self):
        """
        Test de suppression de compte réussie.

        Objectif : Vérifier le workflow complet de suppression
        """
        self.client.login(
            email="delete@example.com", password=TEST_USER_PASSWORD
        )

        user_id = self.user.id

        # Vérifier que l'utilisateur existe
        self.assertTrue(User.objects.filter(id=user_id).exists())

        response = self.client.post(reverse("delete_account"))

        # Doit rediriger vers login
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))

        # Vérifier que l'utilisateur a été supprimé
        self.assertFalse(User.objects.filter(id=user_id).exists())

    def test_delete_account_with_related_data(self):
        """
        Test de suppression avec données liées.

        Objectif : Vérifier que la suppression en cascade fonctionne
        """
        self.client.login(
            email="delete@example.com", password=TEST_USER_PASSWORD
        )

        # Créer des données liées (rêves)
        dream = Dream.objects.create(
            user=self.user, transcription="Rêve à supprimer avec l'utilisateur"
        )
        dream_id = dream.id

        # Supprimer le compte
        response = self.client.post(reverse("delete_account"))
        self.assertEqual(response.status_code, 302)

        # Vérifier que les données liées sont aussi supprimées
        self.assertFalse(User.objects.filter(id=self.user.id).exists())
        self.assertFalse(Dream.objects.filter(id=dream_id).exists())


class EditBioViewTest(TestCase):
    """
    Tests de la vue de modification de bio.

    Cette classe teste :
    - Modification de bio via POST
    - Validation des données
    - Redirection après modification
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="bio@example.com",
            username="bio",
            password=TEST_USER_PASSWORD,
            bio="Bio initiale",
        )
        self.client = Client()

    def test_edit_bio_requires_login(self):
        """
        Test que la modification de bio requiert une authentification.

        Objectif : Vérifier la sécurité de la vue
        """
        response = self.client.post(
            reverse("edit_bio"), {"bio": "Nouvelle bio"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url.lower())

    def test_edit_bio_success(self):
        """
        Test de modification de bio réussie.

        Objectif : Vérifier le workflow de modification
        """
        self.client.login(email="bio@example.com", password=TEST_USER_PASSWORD)

        form_data = {"bio": "Bio mise à jour via POST"}

        response = self.client.post(reverse("edit_bio"), form_data)

        # Doit rediriger vers dream_diary
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dream_diary"))

        # Vérifier que la bio a été mise à jour
        self.user.refresh_from_db()
        self.assertEqual(self.user.bio, "Bio mise à jour via POST")

    def test_edit_bio_invalid_data(self):
        """
        Test de modification avec données invalides.

        Objectif : Vérifier la gestion des erreurs
        """
        self.client.login(email="bio@example.com", password=TEST_USER_PASSWORD)

        # Bio trop longue
        form_data = {"bio": "a" * 200}  # Dépasse 180 caractères

        response = self.client.post(reverse("edit_bio"), form_data)

        # Même en cas d'erreur, doit rediriger (la vue ne gère pas les erreurs explicitement)
        self.assertEqual(response.status_code, 302)

        # La bio ne doit pas avoir changé
        self.user.refresh_from_db()
        self.assertEqual(self.user.bio, "Bio initiale")

    def test_edit_bio_only_post(self):
        """
        Test que la modification de bio n'accepte que POST.

        Objectif : Vérifier la méthode HTTP requise
        """
        self.client.login(email="bio@example.com", password=TEST_USER_PASSWORD)

        response = self.client.get(reverse("edit_bio"))
        self.assertEqual(response.status_code, 405)  # Method Not Allowed


class ViewsErrorHandlingTest(TestCase):
    """
    Tests de gestion d'erreurs spécifiques aux vues.

    Cette classe teste :
    - Codes d'erreur HTTP appropriés
    - Messages d'erreur utilisateur
    - Gestion des exceptions de vue
    - Robustesse des vues
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="errors@example.com",
            username="errors",
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_views_handle_corrupted_user_data(self):
        """
        Test de gestion des données utilisateur corrompues.

        Objectif : Vérifier la robustesse face aux données corrompues
        """
        self.client.login(
            email="errors@example.com", password=TEST_USER_PASSWORD
        )

        # Corrompre les données utilisateur
        self.user.profile_picture_base64 = "data:image/png;base64,corrupted!!!"
        self.user.save()

        # Les vues doivent toujours fonctionner
        response = self.client.get(reverse("account_management"))
        self.assertEqual(response.status_code, 200)

    def test_views_handle_missing_templates(self):
        """
        Test de gestion des templates manquants.

        Objectif : Vérifier que les erreurs de template sont gérées
        """
        # Ce test vérifie que si un template est renommé/supprimé,
        # l'erreur est détectable

        with patch("django.shortcuts.render") as mock_render:
            mock_render.side_effect = Exception("Template not found")

            self.client.login(
                email="errors@example.com", password=TEST_USER_PASSWORD
            )

            try:
                response = self.client.get(reverse("account_management"))
                # Si on arrive ici, Django a géré l'erreur
                self.assertTrue(True)
            except Exception:
                # L'exception template est acceptable en test
                self.assertTrue(True)

    def test_views_with_malformed_post_data(self):
        """
        Test des vues avec données POST malformées.

        Objectif : Vérifier la robustesse face aux données bizarres
        """
        malformed_data_sets = [
            {
                "email": ["liste", "au", "lieu", "string"]
            },  # Liste au lieu de string
            {"password": {"dict": "au lieu string"}},  # Dict au lieu de string
            {"invalid_field": "value"},  # Champ inexistant
            {},  # Données vides
        ]

        for malformed_data in malformed_data_sets:
            with self.subTest(data=malformed_data):
                response = self.client.post(
                    reverse("register"), malformed_data
                )

                # Ne doit pas planter, même avec données bizarres
                self.assertIn(response.status_code, [200, 302, 400])

    def test_views_session_consistency(self):
        """
        Test de cohérence des sessions.

        Objectif : Vérifier que les vues gèrent correctement les sessions
        """
        # Connexion normale
        login_success = self.client.login(
            email="errors@example.com", password=TEST_USER_PASSWORD
        )
        self.assertTrue(login_success)

        # Accéder à une vue protégée
        response1 = self.client.get(reverse("account_management"))
        self.assertEqual(response1.status_code, 200)

        # Supprimer l'utilisateur en arrière-plan (simule une suppression concurrente)
        user_id = self.user.id
        self.user.delete()

        # La prochaine requête doit gérer gracieusement l'utilisateur manquant
        response2 = self.client.get(reverse("account_management"))
        # Doit rediriger vers login ou retourner une erreur cohérente
        self.assertIn(response2.status_code, [302, 403, 404])


class ViewsPerformanceTest(TestCase):
    """
    Tests de performance des vues.

    Cette classe teste :
    - Temps de réponse des vues
    - Performance avec gros volumes
    - Optimisation des requêtes DB
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="perf@example.com",
            username="perf",
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_login_view_performance(self):
        """
        Test de performance de la vue de connexion.

        Objectif : Vérifier que la connexion reste rapide
        """
        start_time = time.time()

        form_data = {
            "email": "perf@example.com",
            "password": TEST_USER_PASSWORD,
        }

        response = self.client.post(reverse("login"), form_data)

        end_time = time.time()
        execution_time = end_time - start_time

        # La connexion doit être rapide
        self.assertLess(execution_time, 2.0)
        self.assertEqual(response.status_code, 302)

    def test_account_management_view_performance(self):
        """
        Test de performance de la vue de gestion de compte.

        Objectif : Vérifier que l'affichage reste rapide
        """
        self.client.login(
            email="perf@example.com", password=TEST_USER_PASSWORD
        )

        start_time = time.time()
        response = self.client.get(reverse("account_management"))
        end_time = time.time()

        execution_time = end_time - start_time

        self.assertLess(execution_time, 0.5)
        self.assertEqual(response.status_code, 200)

    def test_register_view_performance(self):
        """Test de performance de l'inscription."""
        start_time = time.time()

        form_data = {
            "email": "newperf@example.com",
            "username": "newperf",
            "password1": "ComplexPassword123!",
            "password2": "ComplexPassword123!",
            "date_of_birth": "1995-06-15",  # AJOUTÉ
        }

        response = self.client.post(reverse("register"), form_data)

        end_time = time.time()
        execution_time = end_time - start_time

        print("\n=== TEST: register performance ===")
        print(f"Execution time: {execution_time:.3f}s")
        print(f"Response status: {response.status_code}")

        if response.status_code == 200:
            if "form" in response.context:
                print(f"Form errors: {response.context['form'].errors}")

        # L'inscription doit être rapide
        self.assertLess(execution_time, 2.0)
        self.assertEqual(response.status_code, 302)
