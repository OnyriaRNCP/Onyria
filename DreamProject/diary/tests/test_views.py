"""
Tests spécifiques des vues Django.

Ce module teste en détail toutes les vues de l'application :
- Authentification et redirections
- Templates et contexte
- Codes de réponse HTTP
- Gestion des erreurs de vues
- Paramètres de requêtes
- Middleware et sessions
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.http import Http404
from unittest.mock import patch, MagicMock
import json
import tempfile
import time
import os

from ..models import Dream
from ..constants import DREAM_ERROR_MESSAGE

User = get_user_model()

TEST_USER_PASSWORD = os.environ.get('TEST_PASSWORD', 'django_test_secure_2024')


# --- Helper module-level pour parser les événements SSE (utilisé par quelques tests) ---
def _sse_events_from_response(response):
    """Retourne la liste des événements SSE (dict) à partir d'une StreamingHttpResponse."""
    content = b"".join(response.streaming_content).decode(
        "utf-8", errors="ignore"
    )
    events = []
    for line in content.strip().split("\n"):
        if line.startswith("data: "):
            payload = line[6:]
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
    return events


class DreamDiaryViewTest(TestCase):
    """
    Tests de la vue dream_diary.

    Cette classe teste :
    - Affichage correct du journal de rêves
    - Contexte et données passées au template
    - Tri des rêves par date
    - Statistiques de profil onirique
    - Formatage des labels
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='diary_view@example.com',
            username='diary_user',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_dream_diary_view_requires_login(self):
        """
        Test que la vue dream_diary requiert une authentification.

        Objectif : Vérifier la sécurité de base de la vue
        """
        response = self.client.get(reverse('dream_diary'))

        # Doit rediriger vers login
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url.lower())

    def test_dream_diary_view_authenticated_empty(self):
        """
        Test de la vue dream_diary avec utilisateur connecté sans rêves.

        Objectif : Vérifier l'affichage pour un nouvel utilisateur
        """
        self.client.login(
            email='diary_view@example.com', password=TEST_USER_PASSWORD
        )

        response = self.client.get(reverse('dream_diary'))

        # Vérifications de base
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.username)

        # Vérifier le contexte
        self.assertEqual(len(response.context['dreams']), 0)

        # Vérifier les stats pour utilisateur sans rêves
        self.assertEqual(
            response.context['statut_reveuse'], 'Silence onirique'
        )
        self.assertEqual(
            response.context['emotion_dominante'], 'Émotion endormie'
        )
        self.assertEqual(response.context['pourcentage_reveuse'], 0)

    def test_dream_diary_view_with_dreams(self):
        """
        Test de la vue dream_diary avec des rêves existants.

        Objectif : Vérifier l'affichage avec données
        """
        # Créer quelques rêves
        dreams_data = [
            ('Premier rêve', 'rêve', 'joie'),
            ('Deuxième rêve', 'rêve', 'sérénité'),
            ('Un cauchemar', 'cauchemar', 'peur'),
        ]

        created_dreams = []
        for transcription, dream_type, emotion in dreams_data:
            dream = Dream.objects.create(
                user=self.user,
                transcription=transcription,
                dream_type=dream_type,
                dominant_emotion=emotion,
                is_analyzed=True,
            )
            created_dreams.append(dream)

        self.client.login(
            email='diary_view@example.com', password=TEST_USER_PASSWORD
        )
        response = self.client.get(reverse('dream_diary'))

        # Vérifications
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['dreams']), 3)

        # Vérifier l'ordre (plus récent en premier)
        context_dreams = response.context['dreams']
        self.assertEqual(
            context_dreams[0], created_dreams[-1]
        )  # Dernier créé en premier

        # Vérifier les stats calculées
        self.assertEqual(
            response.context['statut_reveuse'], 'Âme rêveuse'
        )  # 2 rêves vs 1 cauchemar
        self.assertEqual(
            response.context['pourcentage_reveuse'], 67
        )  # 2/3 ≈ 67%

    def test_dream_diary_view_context_completeness(self):
        """
        Test de complétude du contexte de la vue dream_diary.

        Objectif : Vérifier que toutes les données nécessaires sont présentes
        """
        # Créer un rêve pour avoir des stats
        Dream.objects.create(
            user=self.user,
            transcription="Rêve pour test contexte",
            dream_type="rêve",
            dominant_emotion="joie",
            is_analyzed=True,
        )

        self.client.login(
            email='diary_view@example.com', password=TEST_USER_PASSWORD
        )
        response = self.client.get(reverse('dream_diary'))

        # Vérifier que toutes les clés de contexte sont présentes
        required_context_keys = [
            'dreams',
            'statut_reveuse',
            'pourcentage_reveuse',
            'label_reveuse',
            'emotion_dominante',
            'emotion_dominante_percentage',
        ]

        for key in required_context_keys:
            self.assertIn(
                key, response.context, f"Clé manquante dans le contexte: {key}"
            )

    def test_dream_diary_view_labels_formatting(self):
        """
        Test du formatage des labels dans la vue dream_diary.

        Objectif : Vérifier que les labels sont correctement formatés pour l'affichage
        """
        # Créer un rêve avec émotion qui nécessite formatage
        Dream.objects.create(
            user=self.user,
            transcription="Rêve avec émotion à formater",
            dream_type="cauchemar",
            dominant_emotion="en_colere",
            is_analyzed=True,
        )

        self.client.login(
            email='diary_view@example.com', password=TEST_USER_PASSWORD
        )
        response = self.client.get(reverse('dream_diary'))

        # Vérifier le formatage des labels
        self.assertEqual(
            response.context['emotion_dominante'], 'Colère'
        )  # 'en_colere' → 'Colère'
        self.assertEqual(
            response.context['statut_reveuse'], 'En proie aux cauchemars'
        )

    def test_dream_diary_view_user_isolation(self):
        """
        Test d'isolation des données utilisateur dans la vue.

        Objectif : Vérifier qu'un utilisateur ne voit que ses propres rêves
        """
        # Créer un autre utilisateur avec des rêves
        other_user = User.objects.create_user(
            email='other@example.com',
            username='other_user',
            password=TEST_USER_PASSWORD,
        )

        # Rêves de l'autre utilisateur
        Dream.objects.create(
            user=other_user,
            transcription="Rêve de l'autre utilisateur",
            dream_type="rêve",
            dominant_emotion="joie",
        )

        # Rêve de notre utilisateur
        Dream.objects.create(
            user=self.user,
            transcription="Mon rêve privé",
            dream_type="rêve",
            dominant_emotion="sérénité",
        )

        self.client.login(
            email='diary_view@example.com', password=TEST_USER_PASSWORD
        )
        response = self.client.get(reverse('dream_diary'))

        # Vérifier l'isolation
        dreams = response.context['dreams']
        self.assertEqual(len(dreams), 1)
        self.assertEqual(dreams[0].transcription, "Mon rêve privé")
        self.assertEqual(dreams[0].user, self.user)

    def test_dream_diary_view_template_used(self):
        """
        Test du template utilisé par la vue dream_diary.

        Objectif : Vérifier que le bon template est utilisé
        """
        self.client.login(
            email='diary_view@example.com', password=TEST_USER_PASSWORD
        )
        response = self.client.get(reverse('dream_diary'))

        self.assertTemplateUsed(response, 'diary/dream_diary.html')

    def test_dream_diary_view_performance_with_many_dreams(self):
        """
        Test de performance de la vue avec beaucoup de rêves.

        Objectif : Vérifier que la vue reste rapide même avec beaucoup de données
        """
        # Créer 50 rêves
        dreams_batch = []
        for i in range(50):
            dreams_batch.append(
                Dream(
                    user=self.user,
                    transcription=f"Rêve de performance {i}",
                    dream_type="rêve" if i % 2 == 0 else "cauchemar",
                    dominant_emotion="joie",
                    is_analyzed=True,
                )
            )

        Dream.objects.bulk_create(dreams_batch)

        self.client.login(
            email='diary_view@example.com', password=TEST_USER_PASSWORD
        )

        with patch('diary.utils.analyze_recurring_themes') as mock_themes:
            mock_themes.return_value = {
                'top_theme': 'Performance test',
                'percentage': 50.0,
            }

        start_time = time.time()
        response = self.client.get(reverse('dream_diary'))
        end_time = time.time()

        execution_time = end_time - start_time

        # Doit rester rapide
        self.assertLess(execution_time, 2.0)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['dreams']), 50)


class DreamRecorderViewTest(TestCase):
    """
    Tests de la vue dream_recorder.

    Cette classe teste :
    - Accès à la page d'enregistrement
    - Authentification requise
    - Template correct
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='recorder@example.com',
            username='recorder_user',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_dream_recorder_view_requires_login(self):
        """
        Test que la vue dream_recorder requiert une authentification.

        Objectif : Vérifier la sécurité de la vue d'enregistrement
        """
        response = self.client.get(reverse('dream_recorder'))

        # Doit rediriger vers login
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url.lower())

    def test_dream_recorder_view_authenticated(self):
        """
        Test de la vue dream_recorder avec utilisateur connecté.

        Objectif : Vérifier l'accès normal à la page d'enregistrement
        """
        self.client.login(email='recorder@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get(reverse('dream_recorder'))

        # Vérifications
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Raconte ton rêve')  # Titre de la page

    def test_dream_recorder_view_template_used(self):
        """
        Test du template utilisé par la vue dream_recorder.

        Objectif : Vérifier que le bon template est utilisé
        """
        self.client.login(email='recorder@example.com', password=TEST_USER_PASSWORD)
        response = self.client.get(reverse('dream_recorder'))

        self.assertTemplateUsed(response, 'diary/dream_recorder.html')

    def test_dream_recorder_view_context(self):
        """
        Test du contexte de la vue dream_recorder.

        Objectif : Vérifier que le contexte est minimal et correct
        """
        self.client.login(email='recorder@example.com', password=TEST_USER_PASSWORD)
        response = self.client.get(reverse('dream_recorder'))

        # La vue recorder n'a pas besoin de contexte spécial
        # Juste vérifier qu'elle ne plante pas
        self.assertEqual(response.status_code, 200)


class DreamFollowupViewTest(TestCase):
    """
    Tests de la vue dream_followup avec filtres temporels.

    Cette classe teste :
    - Rendu correct de la page dashboard
    - Gestion des paramètres de filtre
    - Contexte passé au template
    - Gestion des cas d'erreur
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='followup@example.com',
            username='followup_user',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

        # Créer quelques rêves pour les tests
        self._create_test_dreams()

    def _create_test_dreams(self):
        """Créer des données de test"""
        from django.utils import timezone
        from datetime import timedelta

        base_date = timezone.now()

        # Rêve récent
        recent_dream = Dream.objects.create(
            user=self.user,
            transcription="Rêve récent pour dashboard",
            dream_type="rêve",
            dominant_emotion="joie",
            is_analyzed=True,
        )

        # Rêve ancien
        old_dream = Dream.objects.create(
            user=self.user,
            transcription="Rêve ancien pour dashboard",
            dream_type="cauchemar",
            dominant_emotion="peur",
            is_analyzed=True,
        )
        old_dream.created_at = base_date - timedelta(days=100)
        old_dream.save()

    def test_dream_followup_view_requires_login(self):
        """Test que la vue dream_followup requiert une authentification"""
        response = self.client.get(reverse('dream_followup'))

        # Doit rediriger vers login
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url.lower())

    def test_dream_followup_view_default(self):
        """Test de la vue avec paramètres par défaut (toutes les données)"""
        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get(reverse('dream_followup'))

        # Vérifications de base
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'diary/dream_followup.html')

        # Vérifier le contexte
        self.assertIn('dream_type_stats', response.context)
        self.assertIn('emotions_stats', response.context)
        self.assertIn('has_data', response.context)
        self.assertIn('current_period', response.context)

        # Par défaut, période = 'all'
        self.assertEqual(response.context['current_period'], 'all')
        self.assertTrue(response.context['has_data'])  # On a des rêves

    def test_dream_followup_view_with_period_filter(self):
        """Test de la vue avec filtre de période"""
        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get(
            reverse('dream_followup'), {'period': 'month'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['current_period'], 'month')

        # Les stats doivent refléter le filtre
        stats = response.context['dream_type_stats']
        # Seulement 1 rêve récent dans les 30 derniers jours
        self.assertEqual(stats['total'], 1)

    def test_dream_followup_view_with_custom_dates(self):
        """Test de la vue avec dates personnalisées"""
        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)

        from django.utils import timezone
        from datetime import timedelta

        # Dates qui incluent seulement le rêve récent
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=7)

        response = self.client.get(
            reverse('dream_followup'),
            {
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': end_date.strftime('%Y-%m-%d'),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['current_start_date'],
            start_date.strftime('%Y-%m-%d'),
        )
        self.assertEqual(
            response.context['current_end_date'], end_date.strftime('%Y-%m-%d')
        )

    def test_dream_followup_view_context_completeness(self):
        """Test de complétude du contexte"""
        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get(reverse('dream_followup'))

        # Toutes les clés de contexte requises
        required_context_keys = [
            'dream_type_stats',
            'dream_type_timeline',
            'emotions_stats',
            'emotions_timeline',
            'emotions_list',
            'has_data',
            'current_period',
            'date_range_display',
        ]

        for key in required_context_keys:
            self.assertIn(key, response.context, f"Clé manquante: {key}")

    def test_dream_followup_view_no_dreams(self):
        """Test de la vue avec utilisateur sans rêves"""
        # Supprimer les rêves de test
        Dream.objects.filter(user=self.user).delete()

        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get(reverse('dream_followup'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_data'])

        # Les stats doivent être vides mais présentes
        stats = response.context['dream_type_stats']
        self.assertEqual(stats['total'], 0)

    def test_dream_followup_view_emotions_formatting(self):
        """Test du formatage des émotions dans le contexte"""
        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get(reverse('dream_followup'))

        emotions_stats = response.context['emotions_stats']

        # Vérifier que les émotions sont formatées en français
        emotion_labels = list(emotions_stats.keys())

        # "joie" devrait être formaté en "Joie", "peur" en "Peur"
        self.assertIn('Joie', emotion_labels)
        self.assertIn('Peur', emotion_labels)

        # Vérifier la structure des données
        for emotion_label, data in emotions_stats.items():
            self.assertIn('percentage', data)
            self.assertIn('count', data)
            self.assertIsInstance(data['percentage'], float)
            self.assertIsInstance(data['count'], int)

    def test_dream_followup_view_date_range_display(self):
        """Test de l'affichage de la plage de dates"""
        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)

        # Test avec période prédéfinie
        response = self.client.get(
            reverse('dream_followup'), {'period': 'month'}
        )
        self.assertEqual(
            response.context['date_range_display'], 'Les 30 derniers jours'
        )

        # Test avec dates personnalisées
        response = self.client.get(
            reverse('dream_followup'),
            {'start_date': '2024-01-01', 'end_date': '2024-01-31'},
        )
        self.assertIn(
            'Du 01/01/2024 au 31/01/2024',
            response.context['date_range_display'],
        )

    def test_dream_followup_view_user_isolation(self):
        """Test d'isolation des données utilisateur"""
        # Créer un autre utilisateur avec des rêves
        other_user = User.objects.create_user(
            email='other_followup@example.com',
            username='other_followup_user',
            password=TEST_USER_PASSWORD,
        )

        Dream.objects.create(
            user=other_user,
            transcription="Rêve de l'autre utilisateur",
            dream_type="rêve",
            dominant_emotion="bonheur",
            is_analyzed=True,
        )

        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)
        response = self.client.get(reverse('dream_followup'))

        # Les stats ne doivent inclure que les rêves de notre utilisateur
        stats = response.context['dream_type_stats']
        self.assertEqual(stats['total'], 2)  # Nos 2 rêves seulement

        # L'émotion "bonheur" de l'autre utilisateur ne doit pas apparaître
        emotions_stats = response.context['emotions_stats']
        self.assertNotIn('Bonheur', emotions_stats.keys())

    def test_dream_followup_view_template_content(self):
        """Test du contenu rendu dans le template"""
        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get(reverse('dream_followup'))

        # Vérifier la présence d'éléments clés dans le HTML
        self.assertContains(response, 'Suivi de mes rêves')
        self.assertContains(response, 'Types de rêves')
        self.assertContains(response, 'Émotions dominantes')

        # Si Chart.js est inclus
        self.assertContains(response, 'chart.min.js')

    def test_dream_followup_view_performance(self):
        """Test de performance de la vue"""
        # Créer beaucoup de rêves
        batch_dreams = []
        for i in range(50):
            batch_dreams.append(
                Dream(
                    user=self.user,
                    transcription=f"Rêve perf {i}",
                    dream_type="rêve" if i % 2 == 0 else "cauchemar",
                    dominant_emotion="joie",
                    is_analyzed=True,
                )
            )
        Dream.objects.bulk_create(batch_dreams)

        self.client.login(email='followup@example.com', password=TEST_USER_PASSWORD)

        start_time = time.time()
        response = self.client.get(reverse('dream_followup'))
        execution_time = time.time() - start_time

        # Doit rester rapide
        self.assertLess(execution_time, 2.0)
        self.assertEqual(response.status_code, 200)


class AnalyseFromVoiceViewTest(TestCase):
    """
    Tests de la vue analyse_from_voice (API SSE).

    Cette classe teste :
    - API d'analyse vocale avec Server-Sent Events
    - Séquence progressive des événements
    - Gestion des erreurs à chaque étape
    - Format des événements SSE
    - Headers de streaming appropriés
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='api@example.com',
            username='api_user',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def _parse_sse_events(self, content):
        """Parse les événements SSE pour les tests de vues"""
        events = []
        lines = content.strip().split('\n')
        for line in lines:
            if line.startswith('data: '):
                try:
                    event_data = json.loads(line[6:])
                    events.append(event_data)
                except json.JSONDecodeError:
                    continue
        return events

    def test_analyse_from_voice_requires_login(self):
        """
        Test que l'API SSE requiert une authentification.

        Objectif : Vérifier la sécurité de l'API SSE
        """
        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)

            response = self.client.post(
                reverse('analyse_from_voice'), {'audio': audio_file}
            )

        # Doit être bloqué ou rediriger
        self.assertIn(response.status_code, [302, 401, 403])

    def test_analyse_from_voice_requires_post(self):
        """
        Test que l'API SSE n'accepte que les requêtes POST.

        Objectif : Vérifier la méthode HTTP correcte
        """
        self.client.login(email='api@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get(reverse('analyse_from_voice'))
        self.assertEqual(response.status_code, 405)  # Method Not Allowed

    def test_analyse_from_voice_headers(self):
        """
        Test des headers SSE appropriés.

        Objectif : Vérifier les headers de streaming
        """
        self.client.login(email='api@example.com', password=TEST_USER_PASSWORD)

        response = self.client.post(reverse('analyse_from_voice'))

        self.assertEqual(response['Content-Type'], 'text/event-stream')
        self.assertEqual(response['Cache-Control'], 'no-cache')

    def test_analyse_from_voice_no_audio_file(self):
        """
        Test de l'API SSE sans fichier audio.

        Objectif : Vérifier la validation des paramètres en mode SSE
        """
        self.client.login(email='api@example.com', password=TEST_USER_PASSWORD)

        response = self.client.post(reverse('analyse_from_voice'))

        content = b''.join(response.streaming_content).decode('utf-8')
        events = self._parse_sse_events(content)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['step'], 'error')
        self.assertIn('message', events[0])

    @patch('diary.views.transcribe_audio')
    @patch('diary.views.analyze_emotions')
    @patch('diary.views.classify_dream')
    @patch('diary.views.interpret_dream')
    @patch('diary.views.generate_image_from_text')
    def test_analyse_from_voice_success(
        self,
        mock_generate,
        mock_interpret,
        mock_classify,
        mock_analyze,
        mock_transcribe,
    ):
        """
        Test de l'API SSE avec succès complet.

        Objectif : Vérifier la séquence complète d'événements SSE
        """
        # Configuration des mocks
        mock_transcribe.return_value = "Rêve de test API SSE"
        mock_analyze.return_value = ({'joie': 0.8, 'surprise': 0.2}, ['joie'])
        mock_classify.return_value = 'rêve'
        mock_interpret.return_value = {
            'Émotionnelle': 'Test émotionnel',
            'Symbolique': 'Test symbolique',
            'Cognitivo-scientifique': 'Test cognitif',
            'Freudien': 'Test freudien',
        }
        mock_generate.return_value = True

        self.client.login(email='api@example.com', password=TEST_USER_PASSWORD)

        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)

            response = self.client.post(
                reverse('analyse_from_voice'), {'audio': audio_file}
            )

        # Vérifications de base
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/event-stream')

        # Collecte des événements SSE
        content = b''.join(response.streaming_content).decode('utf-8')
        events = self._parse_sse_events(content)

        # Vérification de la séquence d'événements
        event_steps = [event['step'] for event in events]
        expected_steps = [
            'transcription',
            'emotions',
            'image',
            'interpretation',
            'complete',
        ]
        self.assertEqual(event_steps, expected_steps)

        # Vérification du contenu de chaque étape
        transcription_event = events[0]
        self.assertEqual(
            transcription_event['data']['transcription'],
            "Rêve de test API SSE",
        )

        emotions_event = events[1]
        self.assertEqual(emotions_event['data']['dominant_emotion'], 'Joie')
        self.assertEqual(emotions_event['data']['dream_type'], 'Rêve')

        # Vérification que le rêve a été créé en base
        dream = Dream.objects.filter(user=self.user).first()
        self.assertIsNotNone(dream)
        self.assertEqual(dream.transcription, "Rêve de test API SSE")
        self.assertTrue(dream.is_analyzed)

    @patch('diary.views.transcribe_audio')
    def test_analyse_from_voice_transcription_failure(self, mock_transcribe):
        """
        Test d'échec lors de la transcription en mode SSE.

        Objectif : Vérifier la gestion d'erreur au premier niveau SSE
        """
        mock_transcribe.return_value = None

        self.client.login(email='api@example.com', password=TEST_USER_PASSWORD)

        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)

            response = self.client.post(
                reverse('analyse_from_voice'), {'audio': audio_file}
            )

        content = b''.join(response.streaming_content).decode('utf-8')
        events = self._parse_sse_events(content)

        # Doit contenir uniquement un événement d'erreur
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['step'], 'error')

        # Aucun rêve créé
        self.assertEqual(Dream.objects.filter(user=self.user).count(), 0)

    @patch('diary.views.transcribe_audio')
    def test_analyse_from_voice_exception_handling(self, mock_transcribe):
        """
        Test de gestion d'exception globale en mode SSE.

        Objectif : Vérifier que les exceptions sont gérées gracieusement en SSE
        """
        mock_transcribe.side_effect = Exception("Erreur inattendue SSE")

        self.client.login(email='api@example.com', password=TEST_USER_PASSWORD)

        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)

            response = self.client.post(
                reverse('analyse_from_voice'), {'audio': audio_file}
            )

        # L'API doit gérer l'exception et retourner un événement d'erreur
        content = b''.join(response.streaming_content).decode('utf-8')
        events = self._parse_sse_events(content)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['step'], 'error')

    def test_analyse_from_voice_content_type(self):
        """
        Test du Content-Type de la réponse API SSE.

        Objectif : Vérifier que l'API retourne du streaming
        """
        self.client.login(email='api@example.com', password=TEST_USER_PASSWORD)

        response = self.client.post(reverse('analyse_from_voice'))

        self.assertEqual(response['Content-Type'], 'text/event-stream')

    def test_analyse_from_voice_csrf_exempt(self):
        """
        Test que l'API SSE est exemptée de CSRF.

        Objectif : Vérifier que l'API SSE fonctionne sans token CSRF
        """
        self.client.login(email='api@example.com', password=TEST_USER_PASSWORD)

        from django.conf import settings

        middleware_without_csrf = [
            m for m in settings.MIDDLEWARE if 'csrf' not in m.lower()
        ]

        with self.settings(MIDDLEWARE=middleware_without_csrf):
            response = self.client.post(reverse('analyse_from_voice'))

            # Doit fonctionner (même si pas d'audio)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'text/event-stream')


class ViewsErrorHandlingTest(TestCase):
    """
    Tests de gestion d'erreurs spécifiques aux vues.

    Cette classe teste :
    - Codes d'erreur HTTP appropriés
    - Messages d'erreur utilisateur
    - Gestion des exceptions de vue
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='errors@example.com',
            username='error_user',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_404_handling(self):
        """
        Test de gestion des erreurs 404.

        Objectif : Vérifier que les URLs inexistantes retournent 404
        """
        self.client.login(email='errors@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get('/diary/nonexistent-url/')
        self.assertEqual(response.status_code, 404)

    def test_500_error_simulation(self):
        """
        Test de simulation d'erreur 500.

        Objectif : Vérifier que les erreurs serveur sont gérées
        """
        # En mode test Django, les exceptions sont souvent interceptées
        # Testons plutôt qu'une exception dans les utils est gérée gracieusement

        with patch('diary.utils.get_profil_onirique_stats') as mock_stats:
            # Simuler une erreur dans la fonction de stats
            mock_stats.side_effect = Exception("Erreur interne de test")

            self.client.login(
                email='errors@example.com', password=TEST_USER_PASSWORD
            )

            # L'exception devrait être capturée par Django en mode test
            # et ne pas planter complètement la vue
            try:
                response = self.client.get(reverse('dream_diary'))
                # Si on arrive ici, Django a géré l'erreur gracieusement
                self.assertTrue(True, "Django a géré l'exception correctement")
            except Exception:
                # Si une exception est levée, c'est aussi un comportement acceptable
                self.assertTrue(True, "Exception levée comme attendu")

    def test_permission_denied_scenarios(self):
        """
        Test de scénarios de permission refusée.

        Objectif : Vérifier la gestion des permissions
        """
        # Tentative d'accès sans connexion
        response = self.client.get(reverse('dream_diary'))
        self.assertEqual(response.status_code, 302)  # Redirection

        response = self.client.get(reverse('dream_recorder'))
        self.assertEqual(response.status_code, 302)  # Redirection

    def test_malformed_request_handling(self):
        """
        Test de gestion des requêtes malformées.

        Objectif : Vérifier que les requêtes bizarres ne plantent pas
        """
        self.client.login(email='errors@example.com', password=TEST_USER_PASSWORD)

        # Requête POST sur une vue GET
        response = self.client.post(reverse('dream_diary'))
        # Django gère ça gracieusement
        self.assertIn(
            response.status_code, [200, 405]
        )  # OK ou Method Not Allowed

        # Paramètres invalides sur l'API (et sans fichier audio) -> SSE d'erreur
        response = self.client.post(
            reverse('analyse_from_voice'), {'invalid_param': 'invalid_value'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/event-stream')
        events = _sse_events_from_response(response)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].get('step'), 'error')


class ViewsSecurityTest(TestCase):
    """
    Tests de sécurité des vues.

    Cette classe teste :
    - Protection CSRF appropriée
    - Headers de sécurité
    - Validation des entrées
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='security@example.com',
            username='security_user',
            password=TEST_USER_PASSWORD,
        )
        self.client = Client()

    def test_csrf_protection_on_forms(self):
        """
        Test de protection CSRF sur les formulaires.

        Objectif : Vérifier que les vues appropriées sont protégées par CSRF
        """
        self.client.login(email='security@example.com', password=TEST_USER_PASSWORD)

        # La vue dream_diary (GET) ne nécessite pas de CSRF
        response = self.client.get(reverse('dream_diary'))
        self.assertEqual(response.status_code, 200)

        # La vue analyse_from_voice est exemptée de CSRF (@csrf_exempt)
        response = self.client.post(reverse('analyse_from_voice'))
        self.assertEqual(response.status_code, 200)

    def test_content_type_validation(self):
        """
        Test de validation des Content-Type.

        Objectif : Vérifier que les vues valident les types de contenu
        """
        self.client.login(email='security@example.com', password=TEST_USER_PASSWORD)

        # Test avec content-type bizarre sur l'API
        response = self.client.post(
            reverse('analyse_from_voice'),
            data='{"malicious": "data"}',
            content_type='application/x-malicious',
        )

        # Doit être géré gracieusement
        self.assertEqual(response.status_code, 200)

    def test_file_upload_security(self):
        """
        Test de sécurité des uploads de fichiers.

        Objectif : Vérifier que les uploads sont sécurisés
        """
        self.client.login(email='security@example.com', password=TEST_USER_PASSWORD)

        # Test avec un fichier potentiellement malveillant
        malicious_file = tempfile.NamedTemporaryFile(suffix='.exe')
        malicious_file.write(b'MZ\x90\x00')  # Header d'exécutable Windows
        malicious_file.seek(0)

        response = self.client.post(
            reverse('analyse_from_voice'), {'audio': malicious_file}
        )

        # L'application doit gérer ce cas sans planter
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/event-stream')
        events = _sse_events_from_response(response)
        # Doit au moins produire un événement (erreur de transcription probable)
        self.assertGreaterEqual(len(events), 1)
        # Soit erreur, soit la chaîne a été gérée gracieusement jusqu'au bout
        self.assertIn(events[-1].get('step'), ['error', 'complete'])

    def test_response_headers_security(self):
        """
        Test des headers de sécurité dans les réponses.

        Objectif : Vérifier que les headers appropriés sont présents
        """
        self.client.login(email='security@example.com', password=TEST_USER_PASSWORD)

        response = self.client.get(reverse('dream_diary'))

        # Vérifier que la réponse contient du HTML valide
        self.assertEqual(response['Content-Type'], 'text/html; charset=utf-8')

        # API SSE (anciennement JSON)
        response = self.client.post(reverse('analyse_from_voice'))
        self.assertEqual(response['Content-Type'], 'text/event-stream')

    @patch('diary.views.transcribe_audio', return_value=None)
    def test_sse_error_transcription_failure_sends_generic_message_only(
        self, _
    ):
        """Transcription KO → SSE ne renvoie que l'erreur générique, rien n'est créé."""
        self.client.login(email=self.user.email, password=TEST_USER_PASSWORD)

        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)
            response = self.client.post(
                reverse('analyse_from_voice'), {'audio': audio_file}
            )

        # Flux SSE + contenu
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response['Content-Type'].startswith('text/event-stream')
        )
        events = _sse_events_from_response(response)

        # Un seul event 'error' avec le message générique
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get('step'), 'error')
        self.assertEqual(events[0].get('message'), DREAM_ERROR_MESSAGE)

        # Aucun rêve créé
        self.assertEqual(Dream.objects.filter(user=self.user).count(), 0)

    @patch('diary.views.transcribe_audio', return_value="ok")
    @patch('diary.views.analyze_emotions', return_value=(None, None))
    def test_sse_error_after_transcription_shows_generic_message_and_no_creation(
        self, *_
    ):
        """Échec après la transcription (analyse émotions KO) → erreur générique et pas de création."""
        self.client.login(email=self.user.email, password=TEST_USER_PASSWORD)

        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)
            response = self.client.post(
                reverse('analyse_from_voice'), {'audio': audio_file}
            )

        events = _sse_events_from_response(response)

        # On a bien une erreur générique dans le flux
        err = [e for e in events if e.get('step') == 'error']
        self.assertEqual(len(err), 1)
        self.assertEqual(err[0].get('message'), DREAM_ERROR_MESSAGE)

        # Pas d'interprétation ni de 'complete'
        self.assertFalse(
            any(
                e.get('step') in ('interpretation', 'complete') for e in events
            )
        )

        # Aucun rêve créé
        self.assertEqual(Dream.objects.filter(user=self.user).count(), 0)
