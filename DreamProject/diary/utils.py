import os
import json
import sys
import re
import math
import time
import tempfile
import logging
import httpx
import nltk
import spacy
import unicodedata

from typing import List, Dict
from datetime import datetime, timedelta
from django.utils import timezone
from django.core.files.base import ContentFile
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.conf import settings
from dotenv import load_dotenv
from groq import Groq
from mistralai import Mistral

from nltk.corpus import stopwords
from nltk.stem import SnowballStemmer

from bertopic import BERTopic
from sklearn.feature_extraction.text import CountVectorizer
from sentence_transformers import SentenceTransformer
from umap import UMAP
from hdbscan import HDBSCAN

from collections import Counter, defaultdict
from .models import Dream
from typing import Any, Mapping, Optional
from .constants import (
    EMOTION_LABELS,
    DREAM_TYPE_LABELS,
    THEME_CATEGORIES,
    DREAM_SPECIFIC_STOPWORDS,
)

# Chargement des variables d'environnement
load_dotenv()

logger = logging.getLogger(__name__)

try:
    nlp = spacy.load("fr_core_news_sm")
except OSError:
    logger.warning(
        "Modèle spaCy français non trouvé. Installer avec: python -m spacy download fr_core_news_sm"
    )
    nlp = None

try:
    nltk.download('stopwords', quiet=True)
    FRENCH_STOPWORDS = set(stopwords.words('french'))
    stemmer = SnowballStemmer('french')
except ImportError:
    logger.warning("NLTK non disponible. Installer avec: pip install nltk")
    FRENCH_STOPWORDS = set()
    stemmer = None

# Configuration BERTopic
try:
    # Modèle de sentence embeddings multilingue optimisé pour le français
    embedding_model = SentenceTransformer(
        'paraphrase-multilingual-MiniLM-L12-v2'
    )

    # Vectorizer personnalisé pour filtrer les mots non significatifs
    vectorizer_model = CountVectorizer(
        ngram_range=(1, 2),  # Unigrammes et bigrammes
        stop_words=list(FRENCH_STOPWORDS) if FRENCH_STOPWORDS else None,
        min_df=2,  # Au moins 2 occurrences
        max_df=0.8,  # Maximum 80% des documents
        vocabulary=None,
    )

    # Configuration BERTopic pour les rêves
    bertopic_model = BERTopic(
        embedding_model=embedding_model,
        vectorizer_model=vectorizer_model,
        min_topic_size=2,  # Minimum 2 rêves pour créer un thème
        nr_topics='auto',  # Nombre automatique de thèmes
        calculate_probabilities=True,
        language="french",
    )

    logger.info("BERTopic configuré avec succès")
    BERTOPIC_AVAILABLE = True

except ImportError:
    logger.warning(
        "BERTopic non disponible. Installer avec: pip install bertopic sentence-transformers"
    )
    BERTOPIC_AVAILABLE = False
    bertopic_model = None

# Récupération de la configuration centralisée
AI_CONFIG = settings.AI_CONFIG
BASE_DIR = settings.BASE_DIR

# Clients externes avec garde-fou contre les clés manquantes
groq_client = (
    Groq(
        api_key=settings.GROQ_API_KEY,
        http_client=httpx.Client(
            http2=False, timeout=AI_CONFIG['API_TIMEOUT']
        ),
    )
    if settings.GROQ_API_KEY
    else None
)

mistral_client = (
    Mistral(api_key=settings.MISTRAL_API_KEY)
    if settings.MISTRAL_API_KEY
    else None
)

# ---------- FONCTIONS UTILITAIRES ----------


def read_file(file_path):
    """Lit un fichier depuis /prompt avec encodage UTF-8"""
    path = os.path.join(BASE_DIR, "diary", "prompt", file_path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def softmax(preds):
    """Applique softmax à un dictionnaire de prédictions"""
    exp = {k: math.exp(v) for k, v in preds.items()}
    total = sum(exp.values())
    return {k: v / total for k, v in exp.items()}


def validate_and_fix_interpretation(interpretation_data):
    """
    Valide et corrige le format de l'interprétation si nécessaire.
    - Vérifie la présence des 4 clés attendues.
    - Extrait le texte depuis des objets {contenu: ...} ou {content: ...}.
    - Garantit un format cohérent avec des valeurs texte (str).
    """
    if interpretation_data is None:
        logger.warning("Interprétation None reçue")
        return None

    expected_keys = [
        "Émotionnelle",
        "Symbolique",
        "Cognitivo-scientifique",
        "Freudien",
    ]
    fixed_interpretation = {}

    logger.debug(
        f"Validation interprétation - Clés reçues: {list(interpretation_data.keys())}"
    )

    for key in expected_keys:
        if key in interpretation_data:
            value = interpretation_data[key]

            # Si c'est un objet avec 'contenu', extraire le texte
            if isinstance(value, dict) and 'contenu' in value:
                fixed_interpretation[key] = value['contenu']
                logger.debug(f"Extraction contenu pour {key}")

            # Si c'est un objet avec 'content', extraire le texte
            elif isinstance(value, dict) and 'content' in value:
                fixed_interpretation[key] = value['content']
                logger.debug(f"Extraction content pour {key}")

            # Si c'est déjà une chaîne, la conserver telle quelle
            elif isinstance(value, str):
                fixed_interpretation[key] = value

            else:
                # Coercition robuste en chaîne
                fixed_interpretation[key] = _to_str(value)
                logger.warning(
                    f"Conversion forcée en string pour {key}: {type(value)}"
                )

        else:
            # Clé manquante, ajouter un placeholder explicite
            fixed_interpretation[key] = "Interprétation non disponible"
            logger.warning(f"Clé manquante: {key}")

    logger.debug("Validation interprétation terminée avec succès")
    return fixed_interpretation


# ---------- SYSTÈME DE RETRY ----------


def _is_retryable_transcription_error(err: Exception) -> bool:
    """Détecte les erreurs réseau/temporaires qui méritent un retry"""
    msg = str(err).lower()
    keywords = [
        "connection error",
        "connection reset",
        "connection aborted",
        "timeout",
        "temporarily unavailable",
        "service unavailable",
        "tls",
        "ssl",
        "proxy",
        "rate limit",
        "503",
        "502",
        "429",
    ]
    return any(k in msg for k in keywords)


def _transcribe_via_httpx(file_path: str, language: str = "fr") -> str | None:
    """
    Fallback direct sur l'API Groq (OpenAI-compatible) en HTTP/1.1 via httpx.
    Désactive HTTP/2 pour éviter certains soucis de handshake/proxy.
    """
    if not settings.GROQ_API_KEY:
        logger.error("HTTPX fallback: GROQ_API_KEY manquante")
        return None

    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}"}

    # Multipart form-data — liste de tuples pour gérer les champs répétés
    data = [
        ("model", AI_CONFIG['WHISPER_MODEL']),
        ("prompt", "Specify context or spelling"),
        ("response_format", "json"),
        ("language", language),
        ("temperature", str(AI_CONFIG['DEFAULT_TEMPERATURE'])),
        ("timestamp_granularities[]", "word"),
        ("timestamp_granularities[]", "segment"),
    ]

    try:
        with open(file_path, "rb") as f:
            files = {"file": ("audio.wav", f, "audio/wav")}
            timeout = httpx.Timeout(
                connect=15.0, read=180.0, write=60.0, pool=60.0
            )
            limits = httpx.Limits(
                max_keepalive_connections=5, max_connections=10
            )
            with httpx.Client(
                http2=False, timeout=timeout, limits=limits, trust_env=True
            ) as client:
                r = client.post(url, headers=headers, data=data, files=files)
                r.raise_for_status()
                payload = r.json()
                text = payload.get("text")
                if text:
                    logger.info(f"HTTPX fallback OK - {len(text)} caractères")
                    return text
                logger.error(f"HTTPX fallback: réponse inattendue {payload}")
                return None
    except Exception as e:
        logger.error(f"HTTPX fallback échec: {e}")
        return None


# ---------- TRANSCRIPTION ----------


def transcribe_audio(audio_data, language="fr"):
    """Transcrit un audio en texte avec Whisper de Groq + système retry"""
    logger.info(f"Transcription audio démarrée - {len(audio_data)} bytes")
    start_time = time.time()

    # Garde-fou si la clé est absente ou le client non initialisé
    if not settings.GROQ_API_KEY or groq_client is None:
        logger.error(
            "Échec transcription audio: GROQ_API_KEY manquante ou client non initialisé"
        )
        return None

    temp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix='.wav', delete=False
        ) as temp_file:
            temp_file.write(audio_data)
            temp_file_path = temp_file.name

        last_error = None

        # Système de retry avec backoff exponentiel et configuration centralisée
        for attempt in range(1, AI_CONFIG['TRANSCRIBE_MAX_RETRIES'] + 1):
            try:
                logger.info(
                    f"Transcription tentative {attempt}/{AI_CONFIG['TRANSCRIBE_MAX_RETRIES']}"
                )
                with open(temp_file_path, "rb") as audio_file:
                    transcription = groq_client.audio.transcriptions.create(
                        file=audio_file,
                        model=AI_CONFIG['WHISPER_MODEL'],
                        prompt="Specify context or spelling",
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                        language=language,
                        temperature=AI_CONFIG['DEFAULT_TEMPERATURE'],
                    )

                duration = time.time() - start_time

                # Alertes sur contenu problématique
                if len(transcription.text) < 10:
                    logger.warning(
                        f"Transcription très courte: {len(transcription.text)} caractères"
                    )

                if duration > 5:
                    logger.warning(f"Transcription lente: {duration:.2f}s")

                logger.info(
                    f"Transcription réussie - {len(transcription.text)} caractères en {duration:.2f}s"
                )
                return transcription.text

            except Exception as e:
                last_error = e
                if (
                    _is_retryable_transcription_error(e)
                    and attempt < AI_CONFIG['TRANSCRIBE_MAX_RETRIES']
                ):
                    sleep_s = round(
                        AI_CONFIG['TRANSCRIBE_BACKOFF_BASE'] ** attempt, 2
                    )
                    logger.warning(
                        f"Transcription erreur réseau (retry dans {sleep_s}s): {e}"
                    )
                    time.sleep(sleep_s)
                    continue
                else:
                    logger.error(
                        "Transcription error (%s): %s", type(e).__name__, e
                    )
                    break

        # Fallback HTTPX en dernier recours
        logger.info("Tentative fallback HTTPX pour la transcription…")
        result = _transcribe_via_httpx(temp_file_path, language)

        if result:
            duration = time.time() - start_time
            logger.info(f"Fallback HTTPX réussi en {duration:.2f}s")

        return result

    finally:
        # Nettoyage du fichier temporaire même en cas d'erreur
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.warning(
                    f"Impossible de supprimer le fichier temporaire: {e}"
                )


# ---------- SYSTÈME DE FALLBACK ----------


def safe_mistral_call(model, messages, operation="API call"):
    """
    Appel Mistral sécurisé avec système de fallback automatique

    Args:
        model: Modèle principal à utiliser
        messages: Messages pour l'API
        operation: Description de l'opération (pour les logs)

    Returns:
        Response de l'API ou None si tous les fallbacks échouent
    """
    if mistral_client is None:
        logger.error(
            f"[{operation}] Client Mistral non initialisé - MISTRAL_API_KEY manquante"
        )
        return None

    logger.info(f"[{operation}] Démarrage avec {model}")
    start_time = time.time()

    # Utilisation de la configuration centralisée pour les fallbacks
    models_to_try = [model] + AI_CONFIG['FALLBACK_CHAINS'].get(model, [])
    logger.debug(f"[{operation}] Chaîne de fallback: {models_to_try}")

    for attempt, current_model in enumerate(models_to_try):
        try:
            attempt_start = time.time()
            response = mistral_client.chat.complete(
                model=current_model,
                messages=messages,
                response_format={"type": "json_object"},
            )
            attempt_duration = time.time() - attempt_start

            if attempt > 0:
                logger.warning(
                    f"[{operation}] Fallback utilisé: {current_model} en {attempt_duration:.2f}s"
                )
            else:
                logger.info(
                    f"[{operation}] Succès avec {current_model} en {attempt_duration:.2f}s"
                )

            # Alerte sur performance dégradée
            if attempt_duration > 10:
                logger.warning(
                    f"[{operation}] Performance dégradée: {attempt_duration:.2f}s"
                )

            return response

        except Exception as e:
            error_msg = str(e).lower()
            attempt_duration = time.time() - attempt_start

            # Erreurs qui nécessitent un fallback
            if any(
                keyword in error_msg
                for keyword in [
                    "insufficient_quota",
                    "quota_exceeded",
                    "rate_limit",
                    "model_not_found",
                    "service_unavailable",
                    "timeout",
                ]
            ):
                if "quota" in error_msg:
                    logger.warning(
                        f"[{operation}] QUOTA ATTEINT - {current_model}"
                    )
                elif "rate_limit" in error_msg:
                    logger.warning(
                        f"[{operation}] RATE LIMIT - {current_model}"
                    )
                else:
                    logger.warning(
                        f"[{operation}] Erreur {current_model}: {e}"
                    )

                if attempt == len(models_to_try) - 1:
                    total_duration = time.time() - start_time
                    logger.error(
                        f"[{operation}] Tous les fallbacks échoués après {total_duration:.2f}s"
                    )
                    return None

                continue
            else:
                logger.error(
                    f"[{operation}] Erreur critique {current_model}: {e}"
                )
                raise e

    return None


# ---------- ANALYSE D'ÉMOTIONS ----------


def analyze_emotions(text):
    """Renvoie le score des émotions + l'émotion dominante avec fallback"""
    if not text:  # rajouter une vérification pour éviter les type None errors
        logger.warning("Texte vide reçu pour analyse émotionnelle")
        return None, None
    logger.info(f"Analyse émotionnelle démarrée - {len(text)} caractères")

    system_prompt = read_file("context_emotion.txt")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    response = safe_mistral_call(
        model=AI_CONFIG['EMOTION_MODEL'],
        messages=messages,
        operation="Analyse émotionnelle",
    )

    if response is None:
        logger.error(
            "Échec analyse émotionnelle - tous les modèles indisponibles"
        )
        return None, None

    try:
        # Contrôle de format robuste
        raw = json.loads(response.choices[0].message.content)

        # Certains modèles peuvent renvoyer une liste de paires; on la convertit en dict si possible
        if isinstance(raw, list):
            try:
                raw = dict(raw)
            except Exception:
                logger.error(
                    f"Format inattendu des émotions (liste non convertible): {raw}"
                )
                return None, None

        if not isinstance(raw, dict):
            logger.error(f"Format inattendu des émotions (type={type(raw)})")
            return None, None

        # Cast des valeurs non numériques
        cleaned = {}
        for k, v in raw.items():
            try:
                cleaned[k] = float(v)
            except (TypeError, ValueError):
                logger.warning(f"Score non numérique ignoré pour {k}: {v}")

        if not cleaned:
            logger.error("Aucun score exploitable reçu")
            return None, None

        scores = softmax(cleaned)
        dominant = max(scores.items(), key=lambda x: x[1])

        logger.info(f"Émotion dominante: {dominant[0]} ({dominant[1]:.2f})")
        logger.debug(f"Scores détaillés: {json.dumps(scores, indent=2)}")

        return scores, dominant

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Erreur parsing émotions: {e}")
        return None, None


def classify_dream(emotions):
    """Détermine si le rêve est un cauchemar ou non"""
    if emotions is None:
        logger.warning("Classification impossible - émotions non disponibles")
        return None

    with open(
        os.path.join(BASE_DIR, "diary", "prompt", "reference_emotions.json")
    ) as f:
        ref = json.load(f)

    pos = [emotions[e] for e in ref["positif"] if e in emotions]
    neg = [emotions[e] for e in ref["negatif"] if e in emotions]

    avg_pos = sum(pos) / len(pos or [1])
    avg_neg = sum(neg) / len(neg or [1])

    classification = "cauchemar" if avg_neg > avg_pos else "rêve"
    logger.info(f"Classification: {classification}")
    logger.debug(f"Scores - positif: {avg_pos:.2f}, négatif: {avg_neg:.2f}")

    return classification


# ---------- INTERPRÉTATION ----------


def interpret_dream(text):
    """Demande à Mistral une interprétation du rêve avec fallback et validation"""
    logger.info(f"Interprétation démarrée - {len(text)} caractères")

    system_prompt = read_file("context_interpretation.txt")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    response = safe_mistral_call(
        model=AI_CONFIG['INTERPRETATION_MODEL'],
        messages=messages,
        operation="Interprétation",
    )

    if response is None:
        logger.error("Échec interprétation - tous les modèles indisponibles")
        return None

    try:
        raw_interpretation = json.loads(response.choices[0].message.content)
        logger.debug("Réponse IA reçue, validation en cours...")

        # Valider et corriger le format
        validated_interpretation = validate_and_fix_interpretation(
            raw_interpretation
        )

        if validated_interpretation:
            logger.info("Interprétation générée avec succès")
            return validated_interpretation
        else:
            logger.error("Échec validation interprétation")
            return None

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Erreur parsing interprétation: {e}")
        return None


# ---------- GÉNÉRATION D'IMAGES ----------


def generate_image_from_text(user, prompt_text, dream_instance):
    """
    Génère une image IA à partir du texte du rêve, via agent Mistral.
    Stocke l'image en base64 dans le modèle Dream.
    """
    if mistral_client is None:
        logger.error(
            f"Génération image impossible - MISTRAL_API_KEY manquante"
        )
        return False

    logger.info(f"Génération image pour rêve {dream_instance.id}")
    start_time = time.time()

    try:
        system_instructions = read_file("instructions_image.txt")

        try:
            agent = mistral_client.beta.agents.create(
                model=AI_CONFIG['IMAGE_GENERATION_MODEL'],
                name="Dream Image Agent",
                instructions=system_instructions,
                tools=[{"type": "image_generation"}],
                completion_args={"temperature": 0.3, "top_p": 0.95},
            )

            conversation = mistral_client.beta.conversations.start(
                agent_id=agent.id, inputs=prompt_text
            )

            file_id = next(
                (
                    item.file_id
                    for output in conversation.outputs
                    if hasattr(output, "content")
                    for item in output.content
                    if hasattr(item, "file_id")
                ),
                None,
            )

            if not file_id:
                logger.warning("Aucune image générée par l'agent")
                return False

            image_bytes = mistral_client.files.download(file_id=file_id).read()

            # Stocker en base64 au lieu de fichier
            dream_instance.set_image_from_bytes(image_bytes, format='PNG')
            dream_instance.save()

            duration = time.time() - start_time
            logger.info(f"Image générée avec succès en {duration:.2f}s")
            return True

        except Exception as e:
            error_msg = str(e).lower()
            if any(
                keyword in error_msg
                for keyword in [
                    "insufficient_quota",
                    "quota_exceeded",
                    "rate_limit",
                ]
            ):
                logger.warning(f"Quota image atteint: {e}")
                return False
            else:
                raise e

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Erreur génération image après {duration:.2f}s: {e}")
        return False


# ---------- THEMATIQUE ----------


# Configuration BERTopic avec paramètres ajustés pour petits datasets
try:
    if 'test' in sys.argv or getattr(settings, 'TESTING', False):
        BERTOPIC_AVAILABLE = False
        logger.info("BERTopic désactivé en mode test")
    else:
        # Modèle d'embeddings optimisé pour le français
        embedding_model = SentenceTransformer(
            'paraphrase-multilingual-MiniLM-L12-v2'
        )

        # UMAP avec paramètres pour petits datasets
        umap_model = UMAP(
            n_neighbors=2,  # Très petit pour gérer peu de documents
            n_components=2,  # Réduction à 2D
            min_dist=0.0,
            metric='cosine',
            random_state=42,
        )

        # HDBSCAN avec paramètres très permissifs
        hdbscan_model = HDBSCAN(
            min_cluster_size=2,  # Minimum 2 rêves par cluster
            min_samples=1,  # Très permissif
            metric='euclidean',
            cluster_selection_method='eom',
        )

        BERTOPIC_AVAILABLE = True
        logger.info("BERTopic configuré pour petits datasets")

except ImportError:
    logger.warning("BERTopic non disponible")
    BERTOPIC_AVAILABLE = False


def _preprocess_for_analysis(text: str) -> str:
    """Préprocesse le texte avec spaCy pour analyse thématique"""
    if not nlp or not text:
        return _basic_preprocess(text)

    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)

    doc = nlp(text)
    significant_tokens = []

    for token in doc:
        lemma = token.lemma_.lower()

        # Focus sur les noms principalement + quelques verbes d'action
        if (
            token.pos_ in ['NOUN', 'PROPN']
            or (
                token.pos_ == 'VERB'
                and lemma in ['voler', 'tomber', 'courir', 'fuir', 'nager']
            )
            or (token.pos_ == 'ADJ' and len(lemma) >= 5)
        ):

            if (
                len(lemma) >= 3
                and lemma not in FRENCH_STOPWORDS
                and lemma not in DREAM_SPECIFIC_STOPWORDS
                and not lemma.isdigit()
                and token.is_alpha
                and not any(
                    reject in lemma
                    for reject in ['fair', 'avoir', 'être', 'aller']
                )
            ):

                significant_tokens.append(lemma)

    return ' '.join(significant_tokens)


def _basic_preprocess(text: str) -> str:
    """Préprocessing basique sans spaCy"""
    if not text:
        return ""

    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    tokens = text.split()

    filtered = []
    for token in tokens:
        if (
            len(token) >= 4
            and token not in FRENCH_STOPWORDS
            and token not in DREAM_SPECIFIC_STOPWORDS
            and not token.isdigit()
        ):
            filtered.append(token)

    return ' '.join(filtered)


def _bertopic_analysis(dream_texts: List[str], total_dreams: int):
    """Analyse BERTopic pour datasets moyens/grands (8+ rêves)"""
    if not BERTOPIC_AVAILABLE or total_dreams < 8:
        return None

    # Préprocesser les textes
    preprocessed_texts = []
    for text in dream_texts:
        cleaned = _preprocess_for_analysis(text)
        if len(cleaned.strip()) > 10:
            preprocessed_texts.append(cleaned)

    if len(preprocessed_texts) < 5:
        logger.info("Pas assez de textes valides pour BERTopic, fallback")
        return None

    try:
        # Vectorizer adapté
        vectorizer = CountVectorizer(
            ngram_range=(1, 2),
            stop_words=list(FRENCH_STOPWORDS) if FRENCH_STOPWORDS else None,
            min_df=1,  # Plus permissif
            max_df=0.9,
            max_features=50,  # Limiter pour petits datasets
        )

        # BERTopic avec paramètres adaptés
        topic_model = BERTopic(
            embedding_model=embedding_model,
            umap_model=umap_model,
            hdbscan_model=hdbscan_model,
            vectorizer_model=vectorizer,
            min_topic_size=2,
            nr_topics='auto',
            verbose=False,
        )

        topics, probabilities = topic_model.fit_transform(preprocessed_texts)

        # Analyser les résultats
        topic_info = topic_model.get_topic_info()
        valid_topics = topic_info[topic_info.Topic != -1]

        if len(valid_topics) == 0:
            return None

        # Extraire les thèmes
        theme_results = []
        topic_counts = Counter(topics)

        for topic_id, count in topic_counts.items():
            if topic_id != -1 and count >= 2:
                topic_words = topic_model.get_topic(topic_id)
                if topic_words:
                    # Prendre les 2-3 mots les plus représentatifs
                    top_words = [
                        word for word, score in topic_words[:3] if score > 0.1
                    ]
                    if top_words:
                        theme_name = ' & '.join(
                            top_words[:2]
                        )  # Maximum 2 mots
                        theme_results.append((theme_name, count))

        return sorted(theme_results, key=lambda x: x[1], reverse=True)

    except Exception as e:
        logger.error(f"Erreur BERTopic: {e}")
        return None


def _category_analysis(dream_texts: List[str], total_dreams: int):
    """Analyse par catégories prédéfinies pour petits datasets"""
    theme_document_freq = Counter()

    for dream_text in dream_texts:
        words = _preprocess_for_analysis(dream_text).split()

        # Détecter les catégories présentes dans ce rêve
        detected_categories = set()

        for category, keywords in THEME_CATEGORIES.items():
            for word in words:
                for keyword in keywords:
                    if (
                        word == keyword
                        or keyword in word
                        or word in keyword
                        or (
                            stemmer
                            and len(word) > 3
                            and len(keyword) > 3
                            and stemmer.stem(word) == stemmer.stem(keyword)
                        )
                    ):
                        detected_categories.add(category)
                        break

        # Compter chaque catégorie une fois par rêve
        for category in detected_categories:
            theme_document_freq[category] += 1

    # Retourner les thèmes récurrents
    recurring_themes = [
        (theme, count)
        for theme, count in theme_document_freq.items()
        if count >= 2
    ]
    return sorted(recurring_themes, key=lambda x: x[1], reverse=True)


def analyze_recurring_themes(user, min_dreams=2, min_occurrence=2):
    """Analyse adaptative selon le volume de données"""
    logger.info(f"Analyse thématiques récurrentes user {user.id}")

    dreams = (
        Dream.objects.filter(user=user, transcription__isnull=False)
        .exclude(transcription="")
        .values_list('transcription', flat=True)
    )

    dream_texts = list(dreams)
    total_dreams = len(dream_texts)

    if total_dreams < min_dreams:
        return {
            'top_theme': 'Pas encore de données',
            'percentage': 0,
            'total_dreams': total_dreams,
            'message': f'Au moins {min_dreams} rêves nécessaires',
        }

    # Stratégie adaptative selon le volume
    if total_dreams >= 10:
        logger.info("Dataset important : tentative BERTopic")
        themes = _bertopic_analysis(dream_texts, total_dreams)
        method = "BERTopic"
    else:
        logger.info("Dataset petit : analyse par catégories")
        themes = None
        method = "Catégories"

    # Fallback vers catégories si BERTopic échoue
    if not themes:
        logger.info("Fallback vers analyse par catégories")
        themes = _category_analysis(dream_texts, total_dreams)
        method = "Catégories"

    if not themes:
        return {
            'top_theme': 'Aucune récurrence détectée',
            'percentage': 0,
            'total_dreams': total_dreams,
            'message': 'Pas de thématique récurrente trouvée',
        }

    # Thème principal
    top_theme_name, top_theme_count = themes[0]
    top_theme_percentage = round((top_theme_count / total_dreams) * 100, 1)

    logger.info(
        f"Thème détecté ({method}): {top_theme_name} ({top_theme_percentage}%)"
    )

    return {
        'top_theme': top_theme_name.capitalize(),
        'percentage': top_theme_percentage,
        'total_dreams': total_dreams,
        'all_themes': themes[:10],
        'message': f'{len(themes)} thématiques trouvées ({method})',
    }


# ---------- PROFIL ONYRIQUE ----------


def get_profil_onirique_stats(user):
    """Calcule les statistiques du profil onirique d'un utilisateur"""
    logger.info(f"Calcul profil onirique user {user.id}")

    dreams = Dream.objects.filter(user=user)
    total = dreams.count()

    if total == 0:
        logger.info("Aucun rêve enregistré")
        return {
            "statut_reveuse": "silence onirique",
            "pourcentage_reveuse": 0,
            "label_reveuse": "rêves enregistrés",
            "emotion_dominante": "émotion endormie",
            "emotion_dominante_percentage": 0,
            "thematique_recurrente": "Pas encore de données",
            "thematique_percentage": 0,
        }

    # Statut rêve vs cauchemar
    nb_reves = dreams.filter(dream_type='rêve').count()
    nb_cauchemars = dreams.filter(dream_type='cauchemar').count()

    if nb_reves >= nb_cauchemars:
        statut_reveuse = "âme rêveuse"
        pourcentage = round((nb_reves / total) * 100)
        label = "rêves"
    else:
        statut_reveuse = "en proie aux cauchemars"
        pourcentage = round((nb_cauchemars / total) * 100)
        label = "cauchemars"

    # Émotion dominante
    emotions = dreams.values_list('dominant_emotion', flat=True)
    emotion_counts = Counter(emotions)

    if emotion_counts:
        emotion_dominante, count = emotion_counts.most_common(1)[0]
        emotion_percentage = round((count / total) * 100)
        logger.info(
            f"Profil calculé: {statut_reveuse} ({pourcentage}%), émotion: {emotion_dominante}"
        )
    else:
        emotion_dominante = "émotion endormie"
        emotion_percentage = 0

    # Analyse thématique
    theme_analysis = analyze_recurring_themes(user)

    return {
        "statut_reveuse": statut_reveuse,
        "pourcentage_reveuse": pourcentage,
        "label_reveuse": label,
        "emotion_dominante": emotion_dominante,
        "emotion_dominante_percentage": emotion_percentage,
        "thematique_recurrente": theme_analysis['top_theme'],
        "thematique_percentage": theme_analysis['percentage'],
    }


# ---------- DASHBOARD PERSONNEL ----------


def get_date_filter_queryset(
    user, period=None, start_date=None, end_date=None
):
    """
    Retourne un queryset filtré selon la période choisie

    Args:
        user: L'utilisateur
        period: 'month', '3months', '6months', '1year', 'all' ou None
        start_date: Date de début personnalisée (format YYYY-MM-DD)
        end_date: Date de fin personnalisée (format YYYY-MM-DD)
    """
    queryset = Dream.objects.filter(user=user)

    # Si dates personnalisées fournies
    if start_date and end_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            queryset = queryset.filter(created_at__date__range=[start, end])
            logger.debug(f"Filtre personnalisé: {start} à {end}")
            return queryset
        except ValueError:
            logger.warning(f"Dates invalides: {start_date}, {end_date}")
            # En cas d'erreur, on continue avec le period

    # Filtres prédéfinis
    if period == 'month':
        start_date = timezone.now() - timedelta(days=30)
        queryset = queryset.filter(created_at__gte=start_date)
        logger.debug("Filtre: 30 derniers jours")
    elif period == '3months':
        start_date = timezone.now() - timedelta(days=90)
        queryset = queryset.filter(created_at__gte=start_date)
        logger.debug("Filtre: 3 derniers mois")
    elif period == '6months':
        start_date = timezone.now() - timedelta(days=180)
        queryset = queryset.filter(created_at__gte=start_date)
        logger.debug("Filtre: 6 derniers mois")
    elif period == '1year':
        start_date = timezone.now() - timedelta(days=365)
        queryset = queryset.filter(created_at__gte=start_date)
        logger.debug("Filtre: 1 an")
    else:
        logger.debug("Aucun filtre appliqué")

    return queryset


def get_dream_type_stats_filtered(
    user, period=None, start_date=None, end_date=None
):
    """Version filtrée de get_dream_type_stats"""
    dreams = get_date_filter_queryset(user, period, start_date, end_date)
    total = dreams.count()

    if total == 0:
        return {
            'percentages': {'rêve': 0, 'cauchemar': 0},
            'counts': {'rêve': 0, 'cauchemar': 0},
            'total': 0,
        }

    nb_reves = dreams.filter(dream_type='rêve').count()
    nb_cauchemars = dreams.filter(dream_type='cauchemar').count()

    return {
        'percentages': {
            'rêve': round((nb_reves / total) * 100, 1),
            'cauchemar': round((nb_cauchemars / total) * 100, 1),
        },
        'counts': {'rêve': nb_reves, 'cauchemar': nb_cauchemars},
        'total': total,
    }


def get_dream_type_timeline_filtered(
    user, period=None, start_date=None, end_date=None
):
    """Version filtrée de get_dream_type_timeline"""
    dreams = (
        get_date_filter_queryset(user, period, start_date, end_date)
        .annotate(date_only=TruncDate('created_at'))
        .values('date_only', 'dream_type')
        .annotate(count=Count('id'))
        .order_by('date_only')
    )

    # Organiser les données par date
    timeline_data = {}
    for dream in dreams:
        date_str = dream['date_only'].strftime('%Y-%m-%d')
        if date_str not in timeline_data:
            timeline_data[date_str] = {'rêve': 0, 'cauchemar': 0}
        timeline_data[date_str][dream['dream_type']] = dream['count']

    # Convertir en liste pour le frontend
    timeline_list = []
    for date_str, counts in sorted(timeline_data.items()):
        timeline_list.append(
            {
                'date': date_str,
                'rêve': counts['rêve'],
                'cauchemar': counts['cauchemar'],
            }
        )

    return timeline_list


def get_emotions_stats_filtered(
    user, period=None, start_date=None, end_date=None
):
    """Version filtrée de get_emotions_stats"""
    dreams = get_date_filter_queryset(
        user, period, start_date, end_date
    ).exclude(dominant_emotion__isnull=True)
    total = dreams.count()

    if total == 0:
        return {'percentages': {}, 'counts': {}, 'total': 0}

    emotion_counts = Counter(dreams.values_list('dominant_emotion', flat=True))

    # Calculer les pourcentages
    emotion_percentages = {}
    for emotion, count in emotion_counts.items():
        emotion_percentages[emotion] = round((count / total) * 100, 1)

    return {
        'percentages': emotion_percentages,
        'counts': dict(emotion_counts),
        'total': total,
    }


def get_emotions_timeline_filtered(
    user, period=None, start_date=None, end_date=None
):
    """Version filtrée de get_emotions_timeline"""
    dreams = (
        get_date_filter_queryset(user, period, start_date, end_date)
        .exclude(dominant_emotion__isnull=True)
        .annotate(date_only=TruncDate('created_at'))
        .values('date_only', 'dominant_emotion')
        .annotate(count=Count('id'))
        .order_by('date_only')
    )

    # Organiser les données par date
    timeline_data = {}
    all_emotions = set()

    for dream in dreams:
        date_str = dream['date_only'].strftime('%Y-%m-%d')
        emotion = dream['dominant_emotion']
        all_emotions.add(emotion)

        if date_str not in timeline_data:
            timeline_data[date_str] = {}
        timeline_data[date_str][emotion] = dream['count']

    # Convertir en liste pour le frontend
    timeline_list = []
    for date_str in sorted(timeline_data.keys()):
        entry = {'date': date_str}
        for emotion in all_emotions:
            entry[emotion] = timeline_data[date_str].get(emotion, 0)
        timeline_list.append(entry)

    return timeline_list, list(all_emotions)


def get_themes_stats_filtered(
    user, period=None, start_date=None, end_date=None
):
    """
    Analyse les thématiques récurrentes pour une période donnée
    """
    logger.info(
        f"Analyse thématiques filtrées user {user.id} - period: {period}"
    )

    dreams_queryset = get_date_filter_queryset(
        user, period, start_date, end_date
    )
    dreams = dreams_queryset.filter(transcription__isnull=False).exclude(
        transcription=""
    )

    dream_texts = list(dreams.values_list('transcription', flat=True))
    total_dreams = len(dream_texts)

    if total_dreams < 2:
        return {
            'themes': {},
            'total_dreams': total_dreams,
            'top_theme': None,
            'has_data': False,
            'message': f'Au moins 2 rêves nécessaires pour détecter des thématiques',
        }

    # Utiliser la logique existante d'analyze_recurring_themes mais adaptée
    if total_dreams >= 8 and BERTOPIC_AVAILABLE:
        themes_results = _bertopic_analysis(dream_texts, total_dreams)
        method = "BERTopic"
    else:
        themes_results = _category_analysis(dream_texts, total_dreams)
        method = "Catégories"

    if not themes_results:
        return {
            'themes': {},
            'total_dreams': total_dreams,
            'top_theme': None,
            'has_data': False,
            'message': 'Aucune thématique récurrente détectée',
        }

    # Formatage pour le frontend
    themes_dict = {}
    for theme_name, count in themes_results:
        percentage = round((count / total_dreams) * 100, 1)
        themes_dict[theme_name.capitalize()] = {
            'count': count,
            'percentage': percentage,
        }

    top_theme = {
        'name': themes_results[0][0].capitalize(),
        'count': themes_results[0][1],
        'percentage': round((themes_results[0][1] / total_dreams) * 100, 1),
    }

    logger.info(
        f"Thématiques analysées ({method}): {len(themes_results)} trouvées"
    )

    return {
        'themes': themes_dict,
        'total_dreams': total_dreams,
        'top_theme': top_theme,
        'has_data': True,
        'method': method,
    }


def get_themes_timeline_filtered(
    user, period=None, start_date=None, end_date=None
):
    """
    Analyse l'évolution des thématiques dans le temps
    Retourne les données pour un graphique temporel des thèmes
    """
    logger.info(f"Timeline thématiques user {user.id}")

    dreams_queryset = get_date_filter_queryset(
        user, period, start_date, end_date
    )
    dreams = (
        dreams_queryset.filter(transcription__isnull=False)
        .exclude(transcription="")
        .order_by('created_at')
    )

    if dreams.count() < 2:
        return [], []

    # Grouper les rêves par semaine ou mois selon la période
    if period in ['month', '3months']:
        # Groupement par semaine
        date_format = '%Y-W%U'  # Année-Semaine
        display_format = lambda d: f"Sem {d.strftime('%U')}"
    else:
        # Groupement par mois
        date_format = '%Y-%m'
        display_format = lambda d: d.strftime('%m/%Y')

    # Organiser les rêves par période
    dreams_by_period = defaultdict(list)
    for dream in dreams:
        period_key = dream.created_at.strftime(date_format)
        dreams_by_period[period_key].append(dream.transcription)

    # Analyser chaque période
    timeline_data = []
    all_themes = set()

    for period_key in sorted(dreams_by_period.keys()):
        texts = dreams_by_period[period_key]

        if len(texts) >= 2:
            # Analyse des thèmes pour cette période
            if len(texts) >= 5 and BERTOPIC_AVAILABLE:
                themes_results = _bertopic_analysis(texts, len(texts))
            else:
                themes_results = _category_analysis(texts, len(texts))

            # Préparer les données pour cette période
            period_data = {'period': period_key, 'total_dreams': len(texts)}

            if themes_results:
                for theme_name, count in themes_results:
                    theme_clean = theme_name.capitalize()
                    period_data[theme_clean] = count
                    all_themes.add(theme_clean)

            timeline_data.append(period_data)

    # S'assurer que toutes les périodes ont toutes les thématiques (avec 0 si absent)
    for entry in timeline_data:
        for theme in all_themes:
            if theme not in entry:
                entry[theme] = 0

    return timeline_data, list(all_themes)


def get_theme_distribution_by_emotion(
    user, period=None, start_date=None, end_date=None
):
    """
    Analyse la répartition des thématiques par émotion dominante
    Utile pour comprendre quelles thématiques génèrent quelles émotions
    """
    logger.info(f"Distribution thèmes-émotions user {user.id}")

    dreams_queryset = get_date_filter_queryset(
        user, period, start_date, end_date
    )
    dreams = dreams_queryset.filter(
        transcription__isnull=False, dominant_emotion__isnull=False
    ).exclude(transcription="")

    if dreams.count() < 3:
        return {}

    # Grouper par émotion
    dreams_by_emotion = defaultdict(list)
    for dream in dreams:
        emotion = format_emotion_label(dream.dominant_emotion)
        dreams_by_emotion[emotion].append(dream.transcription)

    # Analyser les thèmes pour chaque émotion
    emotion_themes = {}

    for emotion, texts in dreams_by_emotion.items():
        if len(texts) >= 2:
            if len(texts) >= 5 and BERTOPIC_AVAILABLE:
                themes_results = _bertopic_analysis(texts, len(texts))
            else:
                themes_results = _category_analysis(texts, len(texts))

            if themes_results:
                # Prendre les 3 premiers thèmes pour cette émotion
                emotion_themes[emotion] = [
                    {
                        'name': theme_name.capitalize(),
                        'count': count,
                        'percentage': round((count / len(texts)) * 100, 1),
                    }
                    for theme_name, count in themes_results[:3]
                ]

    return emotion_themes


# ---------- NORMALISATION DES LABELS ----------


def _strip_accents(s: str) -> str:
    """
    Minuscule + suppression des accents + trim pour une comparaison robuste.
    """
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _first_value(val: Any) -> Any:
    """
    Prend la 1ère valeur si val est une liste/tuple, sinon renvoie val tel quel.
    Évite que val=None devienne "" (affichable) plutôt que "None".
    """
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        return _first_value(val[0] if val else "")
    return val


def _to_str(val: Any) -> str:
    """
    Force en string propre:
    - None -> ""
    - bytes -> décodage utf-8 best effort
    - autres -> str(val)
    """
    if val is None:
        return ""
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8", errors="replace")
        except Exception:
            return ""
    return val if isinstance(val, str) else str(val)


# Pré-calcul de mappings normalisés (insensibles aux accents et à la casse)
_EMO_NORM = {_strip_accents(str(k)): v for k, v in EMOTION_LABELS.items()}
_DREAM_NORM = {_strip_accents(str(k)): v for k, v in DREAM_TYPE_LABELS.items()}


def _normalize_label(
    val: Any, mapping: Optional[Mapping[str, str]] = None
) -> str:
    """
    Normalise un label pour l'affichage/API:
    - Accepte clé brute, liste/tuple (on prend le 1er élément)
    - Trim + lookup insensible casse/accents dans le mapping
    - Fallback: capitalize() si clé inconnue
    """
    raw = _to_str(_first_value(val)).strip()
    if not raw:
        return ""
    if mapping is not None:
        # Utilise les tables pré-calculées si on reconnait le mapping
        if mapping is EMOTION_LABELS:
            return _EMO_NORM.get(_strip_accents(raw), raw.capitalize())
        if mapping is DREAM_TYPE_LABELS:
            return _DREAM_NORM.get(_strip_accents(raw), raw.capitalize())
        # Fallback générique (rare) : normalise à la volée
        norm_map = {_strip_accents(str(k)): v for k, v in mapping.items()}
        return norm_map.get(_strip_accents(raw), raw.capitalize())
    return raw.capitalize()


def format_emotion_label(val: Any) -> str:
    """Ex: 'Joïe', 'joie', 'JOIE', 'joie ' -> 'Joie' (via EMOTION_LABELS si présent)"""
    return _normalize_label(val, EMOTION_LABELS)


def format_dream_type_label(val: Any) -> str:
    """Ex: 'CAUCHEMAR', 'cauchemar', 'Cauchemàr' -> 'Cauchemar' (via DREAM_TYPE_LABELS si présent)"""
    return _normalize_label(val, DREAM_TYPE_LABELS)
