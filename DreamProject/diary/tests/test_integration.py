"""
Tests d'intégration bout-en-bout.

Ce module teste les workflows complets de l'application :
- Parcours utilisateur complet (enregistrement → analyse → visualisation)
- Intégration entre vues, modèles et fonctions IA
- Cohérence des données à travers tous les composants
- Évolution des statistiques avec plusieurs rêves
- Isolation parfaite entre utilisateurs
- Gestion d'erreurs dans le workflow complet
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
import json
import tempfile
import time
import os

from ..models import Dream
from ..utils import get_profil_onirique_stats
from ._helpers_sse import sse_to_flat_payload as _sse_to_flat_payload, read_sse_events as _read_sse_events

User = get_user_model()

TEST_USER_PASSWORD = os.environ.get('TEST_PASSWORD', 'django_test_secure_2024')

class CompleteUserJourneyTest(TestCase):
    """
    Tests du parcours utilisateur complet.
    
    Cette classe teste le workflow principal de l'application :
    1. Connexion utilisateur
    2. Accès au journal (vide au début)
    3. Enregistrement d'un nouveau rêve
    4. Analyse complète du rêve
    5. Retour au journal avec statistiques mises à jour
    6. Vérification de la persistence des données
    """
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='journey@example.com',
            username='journey_user',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    @patch('diary.views.transcribe_audio')
    @patch('diary.views.analyze_emotions')
    @patch('diary.views.classify_dream')
    @patch('diary.views.interpret_dream')
    @patch('diary.views.generate_image_from_text')
    def test_complete_user_journey_happy_path(self, mock_generate, mock_interpret, 
                                            mock_classify, mock_analyze, mock_transcribe):
        """
        Test complet du parcours utilisateur - cas nominal.
        
        Objectif : Simuler un utilisateur qui utilise toute l'application de A à Z
        
        Parcours testé :
        1. Connexion utilisateur
        2. Accès au journal de rêves (vide)
        3. Navigation vers l'enregistrement
        4. Enregistrement et analyse d'un rêve
        5. Retour au journal avec le nouveau rêve
        6. Vérification des statistiques mises à jour
        """
        # Configuration des mocks pour un workflow parfait
        mock_transcribe.return_value = "J'ai rêvé que je volais au-dessus d'une ville magnifique illuminée par le soleil couchant"
        mock_analyze.return_value = (
            {'joie': 0.5, 'émerveillement': 0.3, 'sérénité': 0.15, 'liberté': 0.05}, 
            ('joie', 0.5)
        )
        mock_classify.return_value = 'rêve'
        mock_interpret.return_value = {
            'Émotionnelle': 'Ce rêve révèle un état émotionnel très positif et un sentiment de liberté intérieure',
            'Symbolique': 'Le vol symbolise votre désir de transcendance et de dépassement des limitations',
            'Cognitivo-scientifique': 'Ce type de rêve de vol indique une bonne estime de soi et une phase créative',
            'Freudien': 'Le vol peut représenter une sublimation des pulsions et un désir d\'élévation'
        }
        mock_generate.return_value = True
        
        # Étape 1 : Connexion utilisateur
        login_success = self.client.login(email='journey@example.com', password=TEST_USER_PASSWORD)
        self.assertTrue(login_success)
        
        # Étape 2 : Accès au journal de rêves (doit être vide)
        response = self.client.get(reverse('dream_diary'))
        self.assertEqual(response.status_code, 200)
        dreams = response.context['dreams']
        self.assertEqual(len(dreams), 0)
        
        # Vérifier les stats initiales
        stats = response.context
        self.assertEqual(stats.get('statut_reveuse'), 'Silence onirique')
        self.assertEqual(stats.get('emotion_dominante'), 'Émotion endormie')
        self.assertEqual(stats.get('pourcentage_reveuse'), 0)
        
        # Étape 3 : Accès à la page d'enregistrement
        response = self.client.get(reverse('dream_recorder'))
        self.assertEqual(response.status_code, 200)
        
        # Étape 4 : Enregistrement et analyse d'un rêve
        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data_integration_test')
            audio_file.seek(0)
            
            response = self.client.post(reverse('analyse_from_voice'), {
                'audio': audio_file
            })
        
        # Vérifier la réponse d'analyse (SSE → payload aplati)
        self.assertEqual(response.status_code, 200)
        data = _sse_to_flat_payload(response)
        self.assertTrue(data['success'])
        
        # Vérifier le contenu de la réponse
        self.assertIn('transcription', data)
        self.assertIn('dominant_emotion', data)
        self.assertIn('dream_type', data)
        self.assertIn('interpretation', data)
        self.assertEqual(data['transcription'], "J'ai rêvé que je volais au-dessus d'une ville magnifique illuminée par le soleil couchant")
        self.assertEqual(data['dominant_emotion'], 'Joie')  # Format unifié: chaîne
        self.assertEqual(data['dream_type'], 'Rêve')          # Formaté via DREAM_TYPE_LABELS
        
        # Étape 5 : Retour au journal - le rêve doit être là
        response = self.client.get(reverse('dream_diary'))
        self.assertEqual(response.status_code, 200)
        dreams = response.context['dreams']
        self.assertEqual(len(dreams), 1)
        
        dream = dreams[0]
        self.assertEqual(dream.user, self.user)
        self.assertTrue(dream.is_analyzed)
        self.assertIn('volais', dream.transcription)
        
        # Étape 6 : Vérifier les statistiques mises à jour
        stats = response.context
        self.assertEqual(stats.get('statut_reveuse'), 'Âme rêveuse')
        self.assertEqual(stats.get('pourcentage_reveuse'), 100)
        self.assertEqual(stats.get('emotion_dominante'), 'Joie')  # Formaté
        self.assertEqual(stats.get('emotion_dominante_percentage'), 100)
        
        # Vérifier que toutes les fonctions ont été appelées dans l'ordre
        mock_transcribe.assert_called_once()
        mock_analyze.assert_called_once()
        mock_classify.assert_called_once()
        mock_interpret.assert_called_once()
        mock_generate.assert_called_once()

    def test_user_journey_with_multiple_dreams(self):
        """
        Test du parcours utilisateur avec plusieurs rêves.
        
        Objectif : Tester l'évolution des statistiques avec plusieurs rêves
        """
        self.client.login(email='journey@example.com', password=TEST_USER_PASSWORD)
        
        # Créer plusieurs rêves directement pour tester l'évolution des stats
        dreams_data = [
            ('Rêve joyeux dans un jardin fleuri', 'rêve', 'joie'),
            ('Rêve paisible au bord de la mer', 'rêve', 'sérénité'),
            ('Cauchemar avec poursuite', 'cauchemar', 'peur'),
            ('Rêve nostalgique de l\'enfance', 'rêve', 'nostalgie'),
            ('Rêve exaltant de réussite', 'rêve', 'joie'),
        ]
        
        for i, (transcription, dream_type, emotion) in enumerate(dreams_data):
            Dream.objects.create(
                user=self.user,
                transcription=transcription,
                dream_type=dream_type,
                dominant_emotion=emotion,
                is_analyzed=True
            )
            
            # Vérifier l'évolution des stats après chaque rêve
            response = self.client.get(reverse('dream_diary'))
            dreams = response.context['dreams']
            self.assertEqual(len(dreams), i + 1)
        
        # Vérifier les statistiques finales
        final_response = self.client.get(reverse('dream_diary'))
        dreams = final_response.context['dreams']
        stats = final_response.context
        
        self.assertEqual(len(dreams), 5)
        self.assertEqual(stats.get('statut_reveuse'), 'Âme rêveuse')  # 4 rêves vs 1 cauchemar
        self.assertEqual(stats.get('pourcentage_reveuse'), 80)  # 4/5 = 80%
        self.assertEqual(stats.get('emotion_dominante'), 'Joie')  # 2 occurrences de joie
        self.assertEqual(stats.get('emotion_dominante_percentage'), 40)  # 2/5 = 40%

    def test_user_journey_error_recovery(self):
        """
        Test de récupération d'erreurs dans le parcours utilisateur.
        
        Objectif : Vérifier que l'utilisateur peut récupérer après une erreur
        """
        self.client.login(email='journey@example.com', password=TEST_USER_PASSWORD)
        
        # Simuler une première tentative qui échoue
        with patch('diary.views.transcribe_audio', return_value=None):
            with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
                audio_file.write(b'fake_audio_data')
                audio_file.seek(0)
                
                response = self.client.post(reverse('analyse_from_voice'), {
                    'audio': audio_file
                })
            
            data = _sse_to_flat_payload(response)
            self.assertFalse(data['success'])
            self.assertIn('error', data)
        
        # Vérifier qu'aucun rêve n'a été créé
        self.assertEqual(Dream.objects.filter(user=self.user).count(), 0)
        
        # Simuler une seconde tentative qui réussit
        with patch('diary.views.transcribe_audio', return_value="Rêve après récupération"), \
             patch('diary.views.analyze_emotions', return_value=({'joie': 0.8}, ('joie', 0.8))), \
             patch('diary.views.classify_dream', return_value='reve'), \
             patch('diary.views.interpret_dream', return_value={'Émotionnelle': 'Test récupération'}), \
             patch('diary.views.generate_image_from_text', return_value=True):
            
            with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
                audio_file.write(b'fake_audio_data')
                audio_file.seek(0)
                
                response = self.client.post(reverse('analyse_from_voice'), {
                    'audio': audio_file
                })
            
            data = _sse_to_flat_payload(response)
            self.assertTrue(data['success'])
        
        # Vérifier qu'un rêve a été créé après récupération
        self.assertEqual(Dream.objects.filter(user=self.user).count(), 1)
        dream = Dream.objects.get(user=self.user)
        self.assertEqual(dream.transcription, "Rêve après récupération")


class MultiUserIsolationTest(TestCase):
    """
    Tests d'isolation entre utilisateurs multiples.
    
    Cette classe teste que l'application gère correctement plusieurs utilisateurs
    en s'assurant qu'aucune donnée ne fuite entre eux.
    """
    
    def setUp(self):
        self.user1 = User.objects.create_user(
            email='user1@example.com',
            username='user1',
            password=TEST_USER_PASSWORD
        )
        self.user2 = User.objects.create_user(
            email='user2@example.com',
            username='user2',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    def test_complete_user_isolation(self):
        """
        Test d'isolation complète entre utilisateurs.
        
        Objectif : Vérifier qu'aucune donnée ne fuite entre utilisateurs
        """
        # Créer des rêves pour chaque utilisateur
        Dream.objects.create(
            user=self.user1,
            transcription="Rêve privé de l'utilisateur 1",
            dream_type="rêve",
            dominant_emotion="joie"
        )
        
        Dream.objects.create(
            user=self.user2,
            transcription="Rêve privé de l'utilisateur 2",
            dream_type="cauchemar",
            dominant_emotion="peur"
        )
        
        # Test avec utilisateur 1
        self.client.login(email='user1@example.com', password=TEST_USER_PASSWORD)
        response = self.client.get(reverse('dream_diary'))
        
        dreams = response.context['dreams']
        self.assertEqual(len(dreams), 1)
        self.assertEqual(dreams[0].transcription, "Rêve privé de l'utilisateur 1")
        self.assertEqual(dreams[0].user, self.user1)
        
        # Vérifier les stats de l'utilisateur 1
        stats = response.context
        self.assertEqual(stats.get('statut_reveuse'), 'Âme rêveuse')
        
        # Test avec utilisateur 2
        self.client.login(email='user2@example.com', password=TEST_USER_PASSWORD)
        response = self.client.get(reverse('dream_diary'))
        
        dreams = response.context['dreams']
        self.assertEqual(len(dreams), 1)
        self.assertEqual(dreams[0].transcription, "Rêve privé de l'utilisateur 2")
        self.assertEqual(dreams[0].user, self.user2)
        
        # Vérifier les stats de l'utilisateur 2
        stats = response.context
        self.assertEqual(stats.get('statut_reveuse'), 'En proie aux cauchemars')

    def test_statistics_isolation_between_users(self):
        """
        Test d'isolation des statistiques entre utilisateurs.
        
        Objectif : Vérifier que les stats sont calculées uniquement sur les rêves de l'utilisateur
        """
        # User1 : profil très joyeux
        for i in range(5):
            Dream.objects.create(
                user=self.user1,
                transcription=f"Rêve joyeux {i}",
                dream_type="rêve",
                dominant_emotion="joie"
            )
        
        # User2 : profil cauchemardesque
        for i in range(4):
            Dream.objects.create(
                user=self.user2,
                transcription=f"Cauchemar {i}",
                dream_type="cauchemar",
                dominant_emotion="peur"
            )
        
        # Vérifier les stats de user1
        stats1 = get_profil_onirique_stats(self.user1)
        self.assertEqual(stats1['statut_reveuse'], 'âme rêveuse')
        self.assertEqual(stats1['pourcentage_reveuse'], 100)
        self.assertEqual(stats1['emotion_dominante'], 'joie')
        self.assertEqual(stats1['emotion_dominante_percentage'], 100)
        
        # Vérifier les stats de user2
        stats2 = get_profil_onirique_stats(self.user2)
        self.assertEqual(stats2['statut_reveuse'], 'en proie aux cauchemars')
        self.assertEqual(stats2['pourcentage_reveuse'], 100)
        self.assertEqual(stats2['emotion_dominante'], 'peur')
        self.assertEqual(stats2['emotion_dominante_percentage'], 100)

    @patch('diary.views.transcribe_audio')
    @patch('diary.views.analyze_emotions')
    @patch('diary.views.classify_dream')
    @patch('diary.views.interpret_dream')
    @patch('diary.views.generate_image_from_text')
    def test_sequential_users_analysis(self, mock_generate, mock_interpret, mock_classify, mock_analyze, mock_transcribe):
        """
        Test d'analyses séquentielles par plusieurs utilisateurs.
        
        Objectif : Vérifier que les analyses de différents utilisateurs n'interfèrent pas
        """
        # Configuration des mocks
        mock_transcribe.return_value = "Rêve d'analyse"
        mock_analyze.return_value = ({'joie': 0.8}, ('joie', 0.8))
        mock_classify.return_value = 'rêve'
        mock_interpret.return_value = {'Émotionnelle': 'Test séquentiel'}
        mock_generate.return_value = True
        
        results = []
        
        # Analyser pour user1
        self.client.login(email='user1@example.com', password=TEST_USER_PASSWORD)
        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)
            
            response = self.client.post(reverse('analyse_from_voice'), {
                'audio': audio_file
            })
            results.append(('user1@example.com', _sse_to_flat_payload(response)))
        
        # Analyser pour user2  
        self.client.login(email='user2@example.com', password=TEST_USER_PASSWORD)
        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)
            
            response = self.client.post(reverse('analyse_from_voice'), {
                'audio': audio_file
            })
            results.append(('user2@example.com', _sse_to_flat_payload(response)))
        
        # Vérifications (identiques à l'original)
        self.assertEqual(len(results), 2)
        for email, result in results:
            self.assertTrue(result['success'])
        
        # Vérifier l'isolation des données
        user1_dreams = Dream.objects.filter(user=self.user1)
        user2_dreams = Dream.objects.filter(user=self.user2)
        
        self.assertEqual(user1_dreams.count(), 1)
        self.assertEqual(user2_dreams.count(), 1)


class DataConsistencyTest(TestCase):
    """
    Tests de cohérence des données à travers tous les composants.
    
    Cette classe vérifie que les données restent cohérentes
    entre les modèles, les vues, les statistiques et l'affichage.
    """
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='consistency@example.com',
            username='consistency_user',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    def test_data_consistency_across_components(self):
        """
        Test de cohérence des données à travers tous les composants.
        
        Objectif : Vérifier qu'il n'y a pas d'incohérence entre les différentes parties
        """
        # Créer un rêve avec des données spécifiques
        dream = Dream.objects.create(
            user=self.user,
            transcription="Rêve de test de cohérence avec émotions complexes",
            dream_type="reve",
            dominant_emotion="en_colere",
            is_analyzed=True
        )
        
        # Ajouter des données JSON complètes
        emotions_data = {
            "en_colere": 0.6,
            "tristesse": 0.25,
            "surprise": 0.15
        }
        interpretation_data = {
            "Émotionnelle": "Analyse émotionnelle de test",
            "Symbolique": "Analyse symbolique de test",
            "Cognitivo-scientifique": "Analyse cognitive de test",
            "Freudien": "Analyse freudienne de test"
        }
        
        dream.emotions = emotions_data
        dream.interpretation = interpretation_data
        dream.save()
        
        self.client.login(email='consistency@example.com', password=TEST_USER_PASSWORD)
        
        # Tester la cohérence dans la vue journal
        response = self.client.get(reverse('dream_diary'))
        self.assertEqual(response.status_code, 200)
        
        # Vérifier que les données du contexte sont cohérentes
        context_dreams = response.context['dreams']
        self.assertEqual(len(context_dreams), 1)
        
        context_dream = context_dreams[0]
        
        # Vérifier la cohérence des données JSON
        self.assertEqual(context_dream.emotions, emotions_data)
        self.assertEqual(context_dream.interpretation, interpretation_data)
        
        # Vérifier la cohérence des labels formatés dans les stats
        stats = response.context
        # 'en_colere' doit être formaté en 'Colère' dans les stats
        self.assertEqual(stats.get('emotion_dominante'), 'Colère')
        
        # Vérifier que les propriétés du modèle sont cohérentes
        self.assertTrue(context_dream.is_analyzed)
        self.assertEqual(context_dream.dream_type, "reve")  # Valeur brute en DB
        self.assertEqual(context_dream.dominant_emotion, "en_colere")  # Valeur brute en DB

    def test_stats_consistency_with_database(self):
        """
        Test de cohérence des statistiques avec la base de données.
        
        Objectif : Vérifier que les stats reflètent exactement les données DB
        """
        # Créer des rêves avec distribution connue
        dreams_data = [
            ('Rêve 1', 'rêve', 'joie'),      # 1
            ('Rêve 2', 'rêve', 'joie'),      # 2  
            ('Rêve 3', 'rêve', 'tristesse'), # 3
            ('Cauchemar 1', 'cauchemar', 'peur'),  # 4
            ('Rêve 4', 'rêve', 'joie'),      # 5 - joie devient dominante (3/5)
        ]
        
        for transcription, dream_type, emotion in dreams_data:
            Dream.objects.create(
                user=self.user,
                transcription=transcription,
                dream_type=dream_type,
                dominant_emotion=emotion,
                is_analyzed=True
            )
        
        # Calculer les stats via la fonction
        stats = get_profil_onirique_stats(self.user)
        
        # Vérifier manuellement avec la DB
        total_dreams = Dream.objects.filter(user=self.user).count()
        reves_count = Dream.objects.filter(user=self.user, dream_type='rêve').count()
        cauchemars_count = Dream.objects.filter(user=self.user, dream_type='cauchemar').count()
        
        self.assertEqual(total_dreams, 5)
        self.assertEqual(reves_count, 4)
        self.assertEqual(cauchemars_count, 1)
        
        # Vérifier la cohérence des stats
        self.assertEqual(stats['pourcentage_reveuse'], 80)  # 4/5 * 100
        self.assertEqual(stats['statut_reveuse'], 'âme rêveuse')  # Plus de rêves
        
        # Vérifier l'émotion dominante
        from collections import Counter
        emotions = Dream.objects.filter(user=self.user).values_list('dominant_emotion', flat=True)
        emotion_counts = Counter(emotions)
        most_common_emotion = emotion_counts.most_common(1)[0]
        
        self.assertEqual(most_common_emotion[0], 'joie')  # 3 occurrences
        self.assertEqual(most_common_emotion[1], 3)
        self.assertEqual(stats['emotion_dominante'], 'joie')
        self.assertEqual(stats['emotion_dominante_percentage'], 60)  # 3/5 * 100

    def test_label_formatting_consistency(self):
        """
        Test de cohérence du formatage des labels.
        
        Objectif : Vérifier que les labels sont formatés uniformément
        """
        # Créer un rêve avec valeurs brutes
        dream = Dream.objects.create(
            user=self.user,
            transcription="Test formatage labels",
            dream_type="cauchemar",  # Valeur brute
            dominant_emotion="en_colere",  # Valeur brute
            is_analyzed=True
        )
        
        self.client.login(email='consistency@example.com', password=TEST_USER_PASSWORD)
        
        # Test via l'API d'analyse (simulation)
        with patch('diary.views.transcribe_audio', return_value="Test"), \
             patch('diary.views.analyze_emotions', return_value=({'en_colere': 0.8}, ('en_colere', 0.8))), \
             patch('diary.views.classify_dream', return_value='cauchemar'), \
             patch('diary.views.interpret_dream', return_value={'Émotionnelle': 'Test'}), \
             patch('diary.views.generate_image_from_text', return_value=True):
            
            with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
                audio_file.write(b'fake_audio_data')
                audio_file.seek(0)
                
                response = self.client.post(reverse('analyse_from_voice'), {
                    'audio': audio_file
                })
            
            data = _sse_to_flat_payload(response)
            
            # Vérifier le formatage dans l'API
            self.assertEqual(data['dominant_emotion'], 'Colère')  # Format unifié: chaîne
            self.assertEqual(data['dream_type'], 'Cauchemar')       # Formaté
        
        # Test via la vue journal
        response = self.client.get(reverse('dream_diary'))
        stats = response.context
        
        # Vérifier le formatage dans les stats
        self.assertEqual(stats.get('emotion_dominante'), 'Colère')  # Formaté
        # Le statut est calculé, donc peut être différent


class WorkflowRobustnessTest(TestCase):
    """
    Tests de robustesse du workflow complet.
    
    Cette classe vérifie que l'application reste stable
    dans différentes conditions d'utilisation.
    """
    def setUp(self):
        self.user = User.objects.create_user(
            email='robustness@example.com',
            username='robustness_user',
            password=TEST_USER_PASSWORD
        )
        self.client = Client()

    def test_workflow_with_partial_ai_failures(self):
        """
        Transcription OK, mais analyse d'émotions échoue.
        → Le workflow doit échouer proprement et ne rien créer.
        """
        self.client.login(email='robustness@example.com', password=TEST_USER_PASSWORD)

        # Patche les symboles à l'endroit où ils sont utilisés : diary.views
        with patch('diary.views.transcribe_audio', return_value="Transcription OK"), \
             patch('diary.views.analyze_emotions', return_value=(None, None)):
            with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
                audio_file.write(b'fake_audio_data')
                audio_file.seek(0)
                response = self.client.post(reverse('analyse_from_voice'), {'audio': audio_file})
                # IMPORTANT : lire le flux tant que les patchs sont actifs
                data = _sse_to_flat_payload(response)

        self.assertFalse(data['success'])
        self.assertEqual(Dream.objects.filter(user=self.user).count(), 0)

    def test_workflow_with_image_generation_failure(self):
        """
        Échec de génération d'image uniquement.
        → Le workflow doit réussir et créer le rêve sans image.
        """
        # Tout passe sauf l'image
        with patch('diary.views.transcribe_audio', return_value="Rêve sans image"), \
             patch('diary.views.analyze_emotions', return_value=({'joie': 0.8}, ('joie', 0.8))), \
             patch('diary.views.classify_dream', return_value='rêve'), \
             patch('diary.views.interpret_dream', return_value={
                 "Émotionnelle": "Texte.",
                 "Symbolique": "Texte.",
                 "Cognitivo-scientifique": "Texte.",
                 "Freudien": "Texte."
             }), \
             patch('diary.views.generate_image_from_text', return_value=False):
            self.client.login(email='robustness@example.com', password=TEST_USER_PASSWORD)
            with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
                audio_file.write(b'fake_audio_data')
                audio_file.seek(0)
                response = self.client.post(reverse('analyse_from_voice'), {'audio': audio_file})
                # Lire le flux sous patch
                data = _sse_to_flat_payload(response)

        # Le workflow doit être un succès malgré l'image KO
        self.assertTrue(data['success'])

        # Le rêve doit être créé et analysé, sans image
        dream = Dream.objects.get(user=self.user)
        self.assertEqual(dream.transcription, "Rêve sans image")
        self.assertTrue(dream.is_analyzed)
        self.assertFalse(dream.has_image)

    def test_workflow_performance_with_realistic_usage(self):
        """
        Performance de la vue journal avec 30 rêves existants.
        """
        for i in range(30):
            Dream.objects.create(
                user=self.user,
                transcription=f"Rêve numéro {i} avec du contenu détaillé pour simuler un usage réel d'utilisateur",
                dream_type="rêve" if i % 3 != 0 else "cauchemar",
                dominant_emotion="joie" if i % 2 == 0 else "tristesse",
                is_analyzed=True
            )

        self.client.login(email='robustness@example.com', password=TEST_USER_PASSWORD)

        start_time = time.time()
        response = self.client.get(reverse('dream_diary'))
        execution_time = time.time() - start_time

        self.assertLess(execution_time, 5.0)
        self.assertEqual(response.status_code, 200)

        dreams = response.context['dreams']
        self.assertEqual(len(dreams), 30)

        stats = response.context
        self.assertIsNotNone(stats.get('statut_reveuse'))
        self.assertIsInstance(stats.get('pourcentage_reveuse'), int)


    # Contrat SSE : patche sur diary.views (lieu d'utilisation réel)
    @patch('diary.views.transcribe_audio', return_value="Un rêve bref")
    @patch('diary.views.analyze_emotions', return_value=({'joie': 1.0}, ('joie', 1.0)))
    @patch('diary.views.classify_dream', return_value='rêve')
    @patch('diary.views.interpret_dream', return_value={'Émotionnelle': 'OK', 'Symbolique': 'OK', 'Cognitivo-scientifique': 'OK', 'Freudien': 'OK'})
    @patch('diary.views.generate_image_from_text', return_value=False)  # échec image non bloquant
    def test_sse_contract_headers_and_stream(self, *_):
        """Contrat SSE : headers + streaming + payload final bien formé."""
        self.client.login(email='robustness@example.com', password=TEST_USER_PASSWORD)

        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)
            response = self.client.post(reverse('analyse_from_voice'), {'audio': audio_file})

        # Headers / streaming
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/event-stream'))
        self.assertIn('Cache-Control', response)
        self.assertTrue(getattr(response, 'streaming', False) or hasattr(response, 'streaming_content'))
        if response.has_header('X-Accel-Buffering'):
            self.assertEqual(response['X-Accel-Buffering'], 'no')

        # Payload final via helper (lecture complète du flux SSE)
        data = _sse_to_flat_payload(response)
        self.assertIn('success', data)
        self.assertTrue(data['success'])  # image échouée => workflow OK
        self.assertIn('dominant_emotion', data)
        self.assertIsInstance(data['dominant_emotion'], str)

    def test_sse_requires_authentication(self):
        """Accès non authentifié : refus (302/401/403 selon config)."""
        with tempfile.NamedTemporaryFile(suffix='.wav') as audio_file:
            audio_file.write(b'fake_audio_data')
            audio_file.seek(0)
            response = self.client.post(reverse('analyse_from_voice'), {'audio': audio_file})
        self.assertIn(response.status_code, (302, 401, 403))

    def test_sse_get_is_not_allowed(self):
        """Méthode GET interdite sur l’endpoint SSE."""
        response = self.client.get(reverse('analyse_from_voice'))
        self.assertEqual(response.status_code, 405)



