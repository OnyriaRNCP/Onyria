"""
Tests critiques essentiels pour le développement rapide.

Ce module contient les tests les plus importants et rapides à exécuter
pendant le développement pour vérifier que les fonctionnalités de base
fonctionnent correctement.

Usage: python manage.py test diary.tests.test_core

Ces tests doivent s'exécuter en moins de 30 secondes.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
import json
import tempfile
import os

from ..models import Dream
from ..utils import softmax, get_profil_onirique_stats

User = get_user_model()

TEST_USER_PASSWORD = os.environ.get('TEST_PASSWORD', 'django_test_secure_2024')


# === Utilitaire local pour lire les événements SSE ===
def _collect_sse_events(response):
    """
    Lit un StreamingHttpResponse SSE et renvoie une liste d'events (dict).
    Chaque event doit arriver sous la forme:  data: {...}\n\n
    """
    events = []
    for chunk in response.streaming_content:
        try:
            line = chunk.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            events.append(json.loads(payload))
        except Exception:
            # On ignore les lignes non conformes/vides
            continue
    return events


class CoreModelTest(TestCase):
    """
    Tests essentiels du modèle Dream.
    
    Ces tests vérifient les fonctionnalités de base du modèle
    qui sont utilisées partout dans l'application.
    """
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='core@example.com',
            username='coreuser',
            password=TEST_USER_PASSWORD
        )

    def test_dream_creation_basic(self):
        """
        Test critique : Création de base d'un rêve.
        
        Si ce test échoue, l'application ne peut pas fonctionner.
        """
        dream = Dream.objects.create(
            user=self.user,
            transcription="Test critique de création de rêve"
        )
        
        self.assertEqual(dream.user, self.user)
        self.assertEqual(dream.transcription, "Test critique de création de rêve")
        self.assertIsNotNone(dream.date)
        self.assertEqual(dream.dream_type, 'rêve')  # Valeur par défaut
        self.assertFalse(dream.is_analyzed)

    def test_emotions_property_core(self):
        """
        Test critique : Propriété emotions de base.
        
        Cette propriété est utilisée dans toute l'application.
        """
        dream = Dream.objects.create(
            user=self.user,
            transcription="Test emotions"
        )
        
        # Test de base
        emotions = {"joie": 0.8, "tristesse": 0.2}
        dream.emotions = emotions
        dream.save()
        
        self.assertEqual(dream.emotions, emotions)
        
        # Test persistence
        dream.refresh_from_db()
        self.assertEqual(dream.emotions, emotions)

    def test_interpretation_property_core(self):
        """
        Test critique : Propriété interpretation de base.
        
        Cette propriété est essentielle pour l'affichage des analyses.
        """
        dream = Dream.objects.create(
            user=self.user,
            transcription="Test interpretation"
        )
        
        interpretation = {
            "Émotionnelle": "Test émotionnel",
            "Symbolique": "Test symbolique",
            "Cognitivo-scientifique": "Test cognitif",
            "Freudien": "Test freudien"
        }
        
        dream.interpretation = interpretation
        dream.save()
        
        self.assertEqual(dream.interpretation, interpretation)

    def test_short_transcription_core(self):
        """
        Test critique : Propriété short_transcription.
        
        Utilisée dans l'affichage des listes de rêves.
        """
        # Texte court
        short_dream = Dream.objects.create(
            user=self.user,
            transcription="Court"
        )
        self.assertEqual(short_dream.short_transcription, "Court")
        
        # Texte long
        long_text = "a" * 150
        long_dream = Dream.objects.create(
            user=self.user,
            transcription=long_text
        )
        self.assertEqual(long_dream.short_transcription, long_text[:100] + "...")

    def test_has_image_property_core(self):
        """
        Test critique : Propriété has_image.
        
        Utilisée pour l'affichage conditionnel des images.
        """
        dream = Dream.objects.create(
            user=self.user,
            transcription="Test image"
        )
        
        self.assertFalse(dream.has_image)  # Pas d'image par défaut


class CoreUtilsTest(TestCase):
    """
    Tests essentiels des fonctions utilitaires.
    
    Ces fonctions sont critiques pour le bon fonctionnement
    de l'analyse des rêves.
    """

    def test_softmax_function_core(self):
        """
        Test critique : Fonction softmax.
        
        Cette fonction est utilisée pour normaliser les émotions.
        """
        raw_scores = {
            "joie": 2.0,
            "tristesse": 1.0,
            "peur": 0.5
        }
        
        normalized = softmax(raw_scores)
        
        # Vérifications essentielles
        self.assertAlmostEqual(sum(normalized.values()), 1.0, places=5)
        self.assertGreater(normalized["joie"], normalized["tristesse"])
        self.assertGreater(normalized["tristesse"], normalized["peur"])

    def test_profil_stats_core(self):
        """
        Test critique : Statistiques de profil onirique.
        
        Ces stats sont affichées sur la page principale.
        """
        # Cas de base : aucun rêve
        stats = get_profil_onirique_stats(self.user)
        self.assertEqual(stats['statut_reveuse'], "silence onirique")
        self.assertEqual(stats['pourcentage_reveuse'], 0)
        
        # Cas avec un rêve
        Dream.objects.create(
            user=self.user,
            transcription="Premier rêve",
            dream_type="rêve",
            dominant_emotion="joie"
        )
        
        stats = get_profil_onirique_stats(self.user)
        self.assertEqual(stats['statut_reveuse'], 'âme rêveuse')
        self.assertEqual(stats['pourcentage_reveuse'], 100)
        self.assertEqual(stats['emotion_dominante'], 'joie')

    def setUp(self):
        self.user = User.objects.create_user(
            email='core_utils@example.com',
            username='coreutils',
            password=TEST_USER_PASSWORD
        )


class CoreViewsTest(TestCase):
    """
    Tests essentiels des vues principales.
    
    Ces tests vérifient que les pages principales
    se chargent correctement.
    """
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='core_views@example.com',
            username='coreviewsuser',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    def test_dream_diary_view_loads(self):
        """
        Test critique : La page principale se charge.
        
        Si ce test échoue, l'utilisateur ne peut pas accéder à l'app.
        """
        self.client.login(email='core_views@example.com', password=TEST_USER_PASSWORD)
        
        response = self.client.get(reverse('dream_diary'))
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.username)

    def test_dream_recorder_view_loads(self):
        """
        Test critique : La page d'enregistrement se charge.
        
        C'est la fonctionnalité principale de l'app.
        """
        self.client.login(email='core_views@example.com', password=TEST_USER_PASSWORD)
        
        response = self.client.get(reverse('dream_recorder'))
        
        self.assertEqual(response.status_code, 200)

    def test_authentication_required(self):
        """
        Test critique : L'authentification est requise.
        
        Sécurité de base de l'application.
        """
        # Sans authentification
        response = self.client.get(reverse('dream_diary'))
        self.assertEqual(response.status_code, 302)  # Redirection vers login
        
        response = self.client.get(reverse('dream_recorder'))
        self.assertEqual(response.status_code, 302)

    def test_data_isolation_core(self):
        """
        Test critique : Isolation des données utilisateur.
        
        Un utilisateur ne doit voir que ses propres rêves.
        """
        # Créer un second utilisateur
        user2 = User.objects.create_user(
            email='user2@example.com',
            username='user2',
            password=TEST_USER_PASSWORD
        )
        
        # Créer des rêves pour chaque utilisateur
        Dream.objects.create(user=self.user, transcription="Rêve user 1")
        Dream.objects.create(user=user2, transcription="Rêve user 2")
        
        # Connexion user 1
        self.client.login(email='core_views@example.com', password=TEST_USER_PASSWORD)
        response = self.client.get(reverse('dream_diary'))
        
        dreams = response.context['dreams']
        self.assertEqual(len(dreams), 1)
        self.assertEqual(dreams[0].transcription, "Rêve user 1")


class CoreWorkflowTest(TestCase):
    """
    Tests essentiels du workflow principal.
    
    Ces tests vérifient que le processus d'analyse de rêve
    fonctionne de bout en bout (avec mocks).
    """
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='core_workflow@example.com',
            username='coreworkflow',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    @patch('diary.views.transcribe_audio')
    @patch('diary.views.analyze_emotions')
    @patch('diary.views.classify_dream')
    @patch('diary.views.interpret_dream')
    @patch('diary.views.generate_image_from_text')
    def test_complete_analysis_workflow_core(self, mock_generate, mock_interpret, 
                                           mock_classify, mock_analyze, mock_transcribe):
        """
        Test critique : Workflow d'analyse complet (version SSE).
        
        Le test le plus important de l'application.
        Vérifie que l'analyse fonctionne de bout en bout.
        """
        # Configuration des mocks
        mock_transcribe.return_value = "J'ai rêvé d'un oiseau bleu"
        mock_analyze.return_value = ({'joie': 0.8, 'surprise': 0.2}, ('joie', 0.8))
        mock_classify.return_value = 'rêve'
        mock_interpret.return_value = {
            'Émotionnelle': 'Rêve joyeux',
            'Symbolique': 'Oiseau = liberté',
            'Cognitivo-scientifique': 'Consolidation positive',
            'Freudien': 'Désir de liberté'
        }
        mock_generate.return_value = True
        
        # Connexion et test
        self.client.login(email='core_workflow@example.com', password=TEST_USER_PASSWORD)
        
        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)
            
            response = self.client.post(reverse('analyse_from_voice'), {
                'audio': audio_file
            })
        
        # Vérifications: réponse SSE
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/event-stream')

        # Consommer le flux SSE
        events = _collect_sse_events(response)

        # On doit avoir au moins: transcription -> emotions -> image -> interpretation -> complete
        steps = [e.get('step') for e in events]
        self.assertIn('transcription', steps)
        self.assertIn('emotions', steps)
        self.assertIn('interpretation', steps)
        self.assertIn('complete', steps)

        # Vérif contenu de transcription
        trans_evt = next(e for e in events if e.get('step') == 'transcription')
        self.assertIn('transcription', trans_evt['data'])
        self.assertEqual(trans_evt['data']['transcription'], "J'ai rêvé d'un oiseau bleu")

        # Vérif contenu d'émotions (formaté côté vue)
        emo_evt = next(e for e in events if e.get('step') == 'emotions')
        self.assertIn('dominant_emotion', emo_evt['data'])
        self.assertIn('dream_type', emo_evt['data'])
        # La vue renvoie une **chaîne** pour l'émotion formatée, pas une liste
        self.assertEqual(emo_evt['data']['dominant_emotion'], 'Joie')
        self.assertEqual(emo_evt['data']['dream_type'], 'Rêve')

        # Vérif interprétation (dict validé)
        interp_evt = next(e for e in events if e.get('step') == 'interpretation')
        self.assertIn('interpretation', interp_evt['data'])
        self.assertIsInstance(interp_evt['data']['interpretation'], dict)

        # Vérifier qu'un rêve a été créé et marqué analysé
        dream = Dream.objects.get(user=self.user)
        self.assertTrue(dream.is_analyzed)
        self.assertEqual(dream.transcription, "J'ai rêvé d'un oiseau bleu")

    def test_analysis_error_handling_core(self):
        """
        Test critique : Gestion d'erreur dans l'analyse (version SSE).
        
        L'application doit gérer gracieusement les erreurs d'IA.
        """
        with patch('diary.views.transcribe_audio', return_value=None):
            self.client.login(email='core_workflow@example.com', password=TEST_USER_PASSWORD)
            
            with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
                audio_file.write(b'fake_audio_data')
                audio_file.seek(0)
                
                response = self.client.post(reverse('analyse_from_voice'), {
                    'audio': audio_file
                })
            
            # C'est un flux SSE qui doit contenir un event 'error'
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'text/event-stream')

            events = _collect_sse_events(response)
            steps = [e.get('step') for e in events]
            self.assertIn('error', steps)

            # Aucun rêve ne doit être créé
            self.assertEqual(Dream.objects.filter(user=self.user).count(), 0)


class CoreErrorHandlingTest(TestCase):
    """
    Tests critiques de gestion d'erreurs.
    
    Ces tests vérifient que l'application ne plante jamais
    et gère gracieusement les erreurs courantes.
    """
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='core_errors@example.com',
            username='coreerrors',
            password=TEST_USER_PASSWORD
        )

    def test_invalid_json_handling(self):
        """
        Test critique : Gestion des JSON corrompus.
        
        Les propriétés JSON doivent gérer la corruption.
        """
        dream = Dream.objects.create(
            user=self.user,
            transcription="Test JSON corrompu"
        )
        
        # JSON corrompu
        dream.emotions_json = '{"joie": 0.8, "tristesse"'
        dream.interpretation_json = '{"Émotionnelle": "test"'
        dream.save()
        
        # Ne doit pas planter
        emotions = dream.emotions
        interpretation = dream.interpretation
        
        self.assertEqual(emotions, {})
        self.assertEqual(interpretation, {})

    def test_none_values_handling(self):
        """
        Test critique : Gestion des valeurs None.
        
        L'application doit gérer les valeurs None partout.
        """
        dream = Dream.objects.create(
            user=self.user,
            transcription="Test None values"
        )
        
        # Setters avec None
        dream.emotions = None
        dream.interpretation = None
        
        # Ne doit pas planter
        emotions = dream.emotions
        interpretation = dream.interpretation
        
        self.assertEqual(emotions, {})
        self.assertEqual(interpretation, {})

    def test_empty_data_handling(self):
        """
        Test critique : Gestion des données vides.
        
        L'application doit gérer les chaînes vides et données manquantes.
        """
        # Stats avec aucun rêve
        stats = get_profil_onirique_stats(self.user)
        self.assertIsInstance(stats, dict)
        self.assertIn('statut_reveuse', stats)
        
        # Rêve avec transcription vide
        dream = Dream.objects.create(
            user=self.user,
            transcription=""
        )
        
        # Ne doit pas planter
        short_text = dream.short_transcription
        self.assertEqual(short_text, "")


class CoreLabelTest(TestCase):
    """
    Tests critiques des labels et formatage.
    
    Ces tests vérifient que les labels sont correctement
    formatés pour l'affichage utilisateur.
    """
    
    def test_emotion_labels_core(self):
        """
        Test critique : Labels d'émotions essentiels.
        
        Vérifie que les émotions principales ont des labels.
        """
        from ..constants import EMOTION_LABELS
        
        essential_emotions = ['heureux', 'triste', 'apeure', 'en_colere']
        
        for emotion in essential_emotions:
            self.assertIn(emotion, EMOTION_LABELS)
            label = EMOTION_LABELS[emotion]
            self.assertIsInstance(label, str)
            self.assertTrue(label[0].isupper())

    def test_dream_type_labels_core(self):
        """
        Test critique : Labels de types de rêves.
        
        Vérifie que les types de rêves ont des labels corrects.
        """
        from ..constants import DREAM_TYPE_LABELS
        
        self.assertIn('rêve', DREAM_TYPE_LABELS)
        self.assertIn('cauchemar', DREAM_TYPE_LABELS)
        
        self.assertEqual(DREAM_TYPE_LABELS['rêve'], 'Rêve')
        self.assertEqual(DREAM_TYPE_LABELS['cauchemar'], 'Cauchemar')


# Test de sanité général
class CoreSanityTest(TestCase):
    """
    Test de sanité générale.
    
    Vérifie que l'environnement de test fonctionne correctement.
    """
    
    def test_django_setup(self):
        """
        Test de base : Django fonctionne.
        """
        from django.conf import settings
        
        self.assertTrue(settings.configured)
        self.assertIsNotNone(settings.SECRET_KEY)

    def test_database_connection(self):
        """
        Test de base : Base de données accessible.
        """
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            self.assertEqual(result[0], 1)

    def test_imports_work(self):
        """
        Test de base : Tous les imports fonctionnent.
        """
        try:
            from ..models import Dream
            from ..utils import softmax, get_profil_onirique_stats
            from ..constants import EMOTION_LABELS, DREAM_TYPE_LABELS
        except ImportError as e:
            self.fail(f"Import failed: {e}")

    def test_user_model_works(self):
        """
        Test de base : Modèle utilisateur fonctionne.
        """
        user = User.objects.create_user(
            email='sanity@example.com',
            username='sanityuser',
            password=TEST_USER_PASSWORD
        )
        
        self.assertEqual(user.email, 'sanity@example.com')
        self.assertTrue(user.check_password(TEST_USER_PASSWORD))


"""
=== UTILISATION DES TESTS CORE ===

Ces tests sont conçus pour être exécutés fréquemment pendant le développement :

1. LANCER LES TESTS CORE :
   python manage.py test diary.tests.test_core

2. EXÉCUTION RAPIDE (< 30 secondes) :
   Ces tests utilisent principalement des mocks et des données minimales

3. COUVERTURE CRITIQUE :
   - Création de rêves ✓
   - Propriétés JSON ✓  
   - Vues principales ✓
   - Workflow d'analyse ✓
   - Gestion d'erreurs ✓
   - Sécurité de base ✓

4. DÉTECTION PRÉCOCE :
   Si un test core échoue, arrêtez tout et corrigez immédiatement

5. INTÉGRATION CI/CD :
   Ces tests peuvent être exécutés à chaque commit

=== PHILOSOPHIE ===

Les tests core suivent le principe 80/20 :
- 20% des tests qui détectent 80% des problèmes
- Focus sur les fonctionnalités utilisées partout
- Rapidité d'exécution prioritaire
- Feedback immédiat pour le développeur

Si tous les tests core passent, l'application devrait fonctionner
pour les cas d'usage principaux.
"""
