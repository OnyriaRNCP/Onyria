"""
Tests de sécurité et vulnérabilités

Usage: python manage.py test diary.tests.test_security

"""

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
import tempfile
import re
import os

User = get_user_model()

TEST_USER_PASSWORD = os.environ.get('TEST_PASSWORD', 'django_test_secure_2024')



class SecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='security@test.com',
            username='secuser',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    def test_sql_injection_protection(self):
        """Test protection contre l'injection SQL"""
        self.client.login(email='security@test.com', password=TEST_USER_PASSWORD)

        # Tentative d'injection dans les paramètres
        malicious_data = "'; DROP TABLE diary_dream; --"
        response = self.client.get(
            reverse('dream_diary'), {'search': malicious_data}
        )
        # Ne doit pas planter et les données doivent être intactes
        self.assertEqual(response.status_code, 200)

    def test_xss_protection(self):
        """
        Test protection contre XSS

        Ce test vérifie spécifiquement que le contenu malveillant des rêves
        est échappé, sans être perturbé par le JavaScript légitime du template.
        Django fait l'echappement automatiquement
        """
        from ..models import Dream

        self.client.login(email='security@test.com', password=TEST_USER_PASSWORD)

        #  PAYLOAD MALVEILLANT SPÉCIFIQUE
        malicious_content = "<script>alert('XSS Attack!')</script>"
        malicious_identifier = (
            "XSS Attack!"  # Identifiant unique du contenu malveillant
        )

        print(f" Test XSS avec payload: {malicious_content}")

        # Créer un rêve avec le contenu malveillant
        dream = Dream.objects.create(
            user=self.user, transcription=malicious_content
        )

        # Visiter la page où le rêve est affiché
        response = self.client.get(reverse('dream_diary'))
        content = response.content.decode('utf-8')

        #  VÉRIFICATIONS SPÉCIFIQUES AU CONTENU MALVEILLANT
        # 1. L'identifiant du script malveillant ne doit PAS être présent tel quel
        self.assertNotIn(
            malicious_identifier,
            content,
            "DANGER: Contenu malveillant 'XSS Attack!' trouvé non échappé!",
        )

        # 2. Le script malveillant complet ne doit PAS être présent
        self.assertNotIn(
            malicious_content,
            content,
            "DANGER: Script malveillant complet trouvé non échappé!",
        )

        # 3. Vérifier que le contenu est échappé
        # Chercher les versions échappées
        escaped_variants = [
            "&lt;script&gt;alert('XSS Attack!')&lt;/script&gt;",
            "&lt;script&gt;alert(&#x27;XSS Attack!&#x27;)&lt;/script&gt;",
            "&#60;script&#62;alert('XSS Attack!')&#60;/script&#62;",
        ]

        found_escaped = False
        for variant in escaped_variants:
            if variant in content:
                found_escaped = True
                print(f"Contenu correctement échappé: {variant}")
                break

        # Si aucune version échappée n'est trouvée, vérifier que le contenu est au moins stripé
        if not found_escaped:
            # Le contenu pourrait être complètement supprimé (strip_tags)
            # Vérifier que même l'identifiant textuel est absent ou échappé
            if malicious_identifier not in content:
                print(" Contenu malveillant complètement supprimé")
            else:
                self.fail(" Contenu malveillant présent et non échappé!")

        # 4. Vérifier que les balises <script> légitimes du template sont toujours là
        # (pour s'assurer qu'on n'a pas cassé le template)
        legitimate_script_patterns = [
            r'document\.addEventListener\("DOMContentLoaded"',
            r'const tiles = document\.querySelectorAll',
        ]

        legitimate_found = any(
            re.search(pattern, content)
            for pattern in legitimate_script_patterns
        )
        self.assertTrue(
            legitimate_found,
            "JavaScript légitime du template semble avoir été supprimé",
        )

        print(" Test XSS réussi - contenu malveillant neutralisé")

    def test_unauthorized_access(self):
        """Test accès non autorisé aux données d'autres utilisateurs"""
        other_user = User.objects.create_user(
            email='other@test.com',
            username='otheruser',
            password=TEST_USER_PASSWORD,
        )

        from ..models import Dream

        other_dream = Dream.objects.create(
            user=other_user, transcription="Rêve privé de l'autre utilisateur"
        )

        self.client.login(email='security@test.com', password=TEST_USER_PASSWORD)

        # Tentative d'accès direct par ID
        response = self.client.get(f'/diary/dream/{other_dream.id}/')
        self.assertEqual(response.status_code, 404)

    def test_xss_in_interpretation_field(self):
        """
        Test XSS dans le champ interprétation

        Teste l'échappement dans les données JSON d'interprétation
        """
        from ..models import Dream

        self.client.login(email='security@test.com', password=TEST_USER_PASSWORD)

        # Créer un rêve avec interprétation malveillante
        dream = Dream.objects.create(
            user=self.user, transcription="Rêve normal"
        )

        # Ajouter interprétation malveillante
        malicious_interpretation = {
            "Émotionnelle": "<script>alert('XSS in interpretation')</script>",
            "Symbolique": "Analyse normale",
            "Cognitivo-scientifique": "<img src=x onerror=alert('XSS')>",
            "Freudien": "Autre analyse",
        }

        dream.interpretation = malicious_interpretation
        dream.save()

        # Tester la vue détail
        response = self.client.get(
            reverse('dream_detail', kwargs={'dream_id': dream.id})
        )
        content = response.content.decode('utf-8')

        # Vérifications
        self.assertNotIn(
            "alert('XSS in interpretation')",
            content,
            "Script malveillant dans interprétation non échappé!",
        )

        self.assertNotIn(
            "<img src=x onerror=",
            content,
            "Balise image malveillante non échappée!",
        )

        # Le contenu légitime doit être préservé
        self.assertIn("Analyse normale", content)
        self.assertIn("Autre analyse", content)

        print(" Champ interprétation protégé contre XSS")
