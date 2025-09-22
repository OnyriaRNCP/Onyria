"""
Script regroupant toutes nos fonctions utilitaires et de logique
"""

import os
import json
import re
import math
import time
import tempfile
import logging
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import unicodedata
import concurrent.futures
from typing import List
from datetime import datetime, timedelta
from collections import Counter, defaultdict

from typing import Any, Mapping, Optional

import httpx
from django.utils import timezone
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.conf import settings
from dotenv import load_dotenv
from groq import Groq
from mistralai import Mistral
from .metrics.runtime import (
    metric_ok,
    metric_fail,
    metric_fallback,
    metric_retry,
)
from .models import Dream
from .constants import (
    EMOTION_LABELS,
    DREAM_TYPE_LABELS,
    THEME_CATEGORIES,
    DREAM_SPECIFIC_STOPWORDS,
)

# Chargement des variables d'environnement
load_dotenv()

# Logs
logger = logging.getLogger(__name__)

# Récupération de la configuration centralisée
AI_CONFIG = settings.AI_CONFIG
BASE_DIR = settings.BASE_DIR

# Clients externes avec garde-fou contre les clés manquantes
groq_client = (
    Groq(
        api_key=settings.GROQ_API_KEY,
        http_client=httpx.Client(
            http2=False, timeout=AI_CONFIG["API_TIMEOUT"]
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

# Config pour analyse thématique
_nlp_cache = {}
_bertopic_cache = {}


def get_nlp_model():
    """Cache le modèle spaCy pour éviter de le recharger"""
    if "nlp" not in _nlp_cache:
        try:
            import spacy

            _nlp_cache["nlp"] = spacy.load("fr_core_news_sm")
        except OSError:
            logger.warning("Modèle spaCy français non trouvé")
            _nlp_cache["nlp"] = None
    return _nlp_cache["nlp"]


def get_nltk_tools():
    """Import et initialisation différés de NLTK"""
    if "stemmer" not in _nlp_cache:
        try:
            import nltk
            from nltk.corpus import stopwords
            from nltk.stem import SnowballStemmer

            nltk.download("stopwords", quiet=True)
            _nlp_cache["french_stopwords"] = set(stopwords.words("french"))
            _nlp_cache["stemmer"] = SnowballStemmer("french")
        except ImportError:
            logger.warning("NLTK non disponible")
            _nlp_cache["french_stopwords"] = set()
            _nlp_cache["stemmer"] = None

    return _nlp_cache.get("french_stopwords", set()), _nlp_cache.get("stemmer")


def get_bertopic_model():
    """Import et configuration différés de BERTopic"""
    if "bertopic" not in _bertopic_cache:
        try:
            from bertopic import BERTopic
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.decomposition import PCA
            from sklearn.cluster import KMeans

            # Configuration simplifiée : utiliser TF-IDF + PCA + KMeans

            # Vectorizer TF-IDF
            vectorizer_model = TfidfVectorizer(
                ngram_range=(1, 2),
                max_features=100,  # Limiter les features
                min_df=1,
                max_df=0.8,
                stop_words=None,  # On gère les stopwords dans le preprocessing
            )

            # PCA pour réduction dimensionnelle
            dimensionality_model = PCA(n_components=5, random_state=42)

            # KMeans pour clustering
            cluster_model = KMeans(n_clusters=3, random_state=42, n_init=10)

            _bertopic_cache["bertopic"] = BERTopic(
                vectorizer_model=vectorizer_model,
                umap_model=dimensionality_model,
                hdbscan_model=cluster_model,
                min_topic_size=2,
                nr_topics=3,  # Fixer le nombre de topics
                verbose=False,
            )
            _bertopic_cache["available"] = True

        except ImportError as e:
            logger.warning("BERTopic simplifié non disponible: %s", e)
            _bertopic_cache["bertopic"] = None
            _bertopic_cache["available"] = False

    return _bertopic_cache["bertopic"], _bertopic_cache["available"]


# ---------- FONCTIONS UTILITAIRES ----------


def read_file(file_path):
    """Lit un fichier depuis /prompt avec encodage UTF-8"""
    path = os.path.join(BASE_DIR, "diary", "prompt", file_path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def softmax(preds):
    """Applique softmax à un dictionnaire de prédictions"""
    if not preds:
        return {}
    m = max(preds.values())
    exp = {k: math.exp(v - m) for k, v in preds.items()}
    total = sum(exp.values()) or 1.0
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
            if isinstance(value, dict) and "contenu" in value:
                fixed_interpretation[key] = value["contenu"]
                logger.debug(f"Extraction contenu pour {key}")

            # Si c'est un objet avec 'content', extraire le texte
            elif isinstance(value, dict) and "content" in value:
                fixed_interpretation[key] = value["content"]
                logger.debug(f"Extraction content pour {key}")

            # Si c'est déjà une chaîne, la conserver telle quelle
            elif isinstance(value, str):
                fixed_interpretation[key] = value

            else:
                # Rejet immédiat si type invalide
                logger.warning(
                    f"Type invalide pour {key}: {type(value)} - interprétation rejetée"
                )
                return None

        else:
            # Rejet immédiat si clé manquante
            logger.warning(f"Clé manquante: {key} - interprétation rejetée")
            return None

    # Vérifier que toutes les valeurs sont des strings non vides
    for key, value in fixed_interpretation.items():
        if not isinstance(value, str) or not value.strip():
            logger.warning(
                f"Valeur vide ou invalide pour {key} - interprétation rejetée"
            )
            return None

    logger.debug("Validation interprétation terminée avec succès")
    return fixed_interpretation


def _map_reason_from_msg(msg: str, status_code: int | None = None) -> str:
    """
    Mappe un message d'erreur ou un code HTTP vers une raison standardisée.
    - Cherche dans les mots-clés de AI_CONFIG['ERROR_REASON_KEYWORDS']
    - Fallback sur status_code (ex: 429 → rate_limit)
    - Retourne 'unknown' si rien ne correspond
    """
    if not msg:
        msg = ""
    msg = msg.lower()

    for reason, keywords in AI_CONFIG.get("ERROR_REASON_KEYWORDS", {}).items():
        if any(k in msg for k in keywords):
            return reason

    if status_code == 429:
        return "rate_limit"

    return "unknown"


# ---------- SYSTÈME DE RETRY/FALLBACK pour la transcription ----------


def _is_retryable_transcription_error(err: Exception) -> bool:
    """Détecte les erreurs réseau/temporaires qui méritent un retry"""
    msg = str(err).lower()
    return any(k in msg for k in AI_CONFIG["TRANSCRIBE_RETRYABLE_KEYWORDS"])


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
        ("model", AI_CONFIG["WHISPER_MODEL"]),
        ("prompt", "Specify context or spelling"),
        ("response_format", "json"),
        ("language", language),
        ("temperature", str(AI_CONFIG["DEFAULT_TEMPERATURE"])),
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


def validate_transcription_length(transcription):
    """
    Valide si la transcription est suffisante pour l'analyse
    Retourne (is_valid, error_message)
    """
    if not transcription or not transcription.strip():
        return (
            False,
            "Aucun contenu détecté. Veuillez réessayer votre enregistrement.",
        )

    # Nettoyer le texte (enlever espaces, ponctuation basique)
    clean_text = (
        transcription.strip()
        .replace(".", "")
        .replace(",", "")
        .replace("!", "")
        .replace("?", "")
    )
    words = clean_text.split()

    # Minimum 5 mots pour une analyse cohérente
    if len(words) < 5:
        return (
            False,
            "Enregistrement trop court. Décrivez votre rêve avec plus de détails pour une meilleure analyse.",
        )

    return True, None


def transcribe_audio(audio_data, language="fr"):
    """Transcrit un audio en texte avec Whisper de Groq + système retry + validation longueur"""
    logger.info(f"Transcription audio démarrée - {len(audio_data)} bytes")
    start_time = time.time()

    # Garde-fou si la clé est absente ou le client non initialisé
    if not settings.GROQ_API_KEY or groq_client is None:
        logger.error(
            "Échec transcription audio: GROQ_API_KEY manquante ou client non initialisé"
        )
        metric_fail(
            "groq",
            "transcribe",
            int((time.time() - start_time) * 1000),
            reason="no_api_key",
        )
        return None

    temp_file_path = None
    last_error = None
    total_retry_count = 0
    total_backoff_ms = 0

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False
        ) as temp_file:
            temp_file.write(audio_data)
            temp_file_path = temp_file.name

        # Système de retry avec backoff exponentiel et configuration centralisée
        for attempt in range(1, AI_CONFIG["MAX_ATTEMPTS"] + 1):
            try:
                logger.info(
                    f"Transcription tentative {attempt}/{AI_CONFIG['MAX_ATTEMPTS']}"
                )

                with open(temp_file_path, "rb") as audio_file:
                    transcription = groq_client.audio.transcriptions.create(
                        file=audio_file,
                        model=AI_CONFIG["WHISPER_MODEL"],
                        prompt="Specify context or spelling",
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                        language=language,
                        temperature=AI_CONFIG["DEFAULT_TEMPERATURE"],
                    )

                duration = time.time() - start_time

                # Alertes sur contenu problématique
                if len(transcription.text) < 10:
                    logger.warning(
                        f"Transcription très courte: {len(transcription.text)} caractères"
                    )
                if duration > 5:
                    logger.warning(f"Transcription lente: {duration:.2f}s")

                # VALIDATION DE LA LONGUEUR DE TRANSCRIPTION
                is_valid, error_message = validate_transcription_length(
                    transcription.text
                )
                if not is_valid:
                    logger.warning(
                        f"Transcription trop courte rejetée: '{transcription.text[:50]}...'"
                    )
                    metric_retry(
                        "groq",
                        "transcribe",
                        total_retry_count,
                        total_backoff_ms,
                    )
                    metric_fail(
                        "groq",
                        "transcribe",
                        int((time.time() - start_time) * 1000),
                        reason="too_short",
                    )
                    return {"error": "too_short", "message": error_message}

                logger.info(
                    f"Transcription valide - {len(transcription.text)} caractères en {duration:.2f}s"
                )
                metric_retry(
                    "groq", "transcribe", total_retry_count, total_backoff_ms
                )
                metric_ok("groq", "transcribe", int(duration * 1000))
                return transcription.text

            except Exception as e:
                last_error = e
                if (
                    _is_retryable_transcription_error(e)
                    and attempt < AI_CONFIG["MAX_ATTEMPTS"]
                ):
                    sleep_s = round(AI_CONFIG["BACKOFF_BASE"] ** attempt, 2)
                    sleep_ms = int(sleep_s * 1000)

                    # Compter les retries et backoff
                    total_retry_count += 1
                    total_backoff_ms += sleep_ms

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

        # Enregistrer le fallback HTTPX
        metric_fallback("groq", "transcribe", 1)

        result = _transcribe_via_httpx(temp_file_path, language)

        if result:
            duration = time.time() - start_time
            logger.info(f"Fallback HTTPX réussi en {duration:.2f}s")

            # VALIDATION DE LA LONGUEUR POUR LE FALLBACK HTTPX AUSSI
            is_valid, error_message = validate_transcription_length(result)
            if not is_valid:
                logger.warning(
                    f"Transcription HTTPX trop courte rejetée: '{result[:50]}...'"
                )
                metric_retry(
                    "groq", "transcribe", total_retry_count, total_backoff_ms
                )
                metric_fail(
                    "groq",
                    "transcribe",
                    int((time.time() - start_time) * 1000),
                    reason="too_short",
                )
                return {"error": "too_short", "message": error_message}

            metric_retry(
                "groq", "transcribe", total_retry_count, total_backoff_ms
            )
            metric_ok("groq", "transcribe", int(duration * 1000))
            return result
        else:
            duration = time.time() - start_time
            if last_error is not None:
                reason = _map_reason_from_msg(str(last_error), None)
            else:
                reason = "httpx_fallback_failed"

            metric_retry(
                "groq", "transcribe", total_retry_count, total_backoff_ms
            )
            metric_fail(
                "groq", "transcribe", int(duration * 1000), reason=reason
            )
            return None

    finally:
        # Nettoyage du fichier temporaire même en cas d'erreur
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.warning(
                    f"Impossible de supprimer le fichier temporaire: {e}"
                )


# ---------- SYSTÈME DE FALLBACK pour Mistral ----------

# Concrétisation paramétrable depuis settings.AI_CONFIG
try:
    FALLBACK_STATUS = set(AI_CONFIG["ANALYZE_ERROR_STATUS"])
    FALLBACK_KEYWORDS = tuple(AI_CONFIG["ANALYZE_FALLBACK_KEYWORDS"])
except KeyError as e:
    logger.error(
        f"AI_CONFIG manquant: {e}. Règles de fallback vides par sécurité."
    )
    FALLBACK_STATUS = set()
    FALLBACK_KEYWORDS = tuple()


def _extract_status_and_text(e: Exception):
    status = getattr(e, "status_code", None)
    body_text = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        status = getattr(resp, "status_code", status)
        try:
            body_text = resp.text or ""
        except Exception:
            body_text = ""
    return status, body_text


def safe_mistral_call(model, messages, operation="API call"):
    """
    Appel Mistral sécurisé avec système de fallback automatique + timeout applicatif.
    On essaie le modèle principal puis les modèles de fallback,
    avec un délai (backoff) entre chaque tentative.
    """

    if mistral_client is None:
        logger.error(
            f"[{operation}] Client Mistral non initialisé - MISTRAL_API_KEY manquante"
        )
        return None

    logger.info(f"[{operation}] Démarrage avec {model}")
    start_time = time.time()

    # Liste des modèles à essayer : principal + fallbacks
    models_to_try = [model] + AI_CONFIG["FALLBACK_CHAINS"].get(model, [])
    logger.debug(f"[{operation}] Chaîne de fallback: {models_to_try}")

    operation_key = operation.lower().replace(" ", "_")

    for attempt, current_model in enumerate(models_to_try):
        try:
            attempt_start = time.time()

            # Exécuter l'appel Mistral avec un timeout applicatif
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=1
            ) as executor:
                future = executor.submit(
                    mistral_client.chat.complete,
                    model=current_model,
                    messages=messages,
                    response_format={"type": "json_object"},
                )
                response = future.result(
                    timeout=AI_CONFIG["API_TIMEOUT"]
                )  # timeout appliqué

            attempt_duration = time.time() - attempt_start

            # Succès : on enregistre combien de fallbacks ont été faits (0 = pas de fallback)
            metric_fallback("mistral", operation_key, attempt)

            if attempt > 0:
                logger.warning(
                    f"[{operation}] Fallback utilisé: {current_model} en {attempt_duration:.2f}s"
                )
            else:
                logger.info(
                    f"[{operation}] Succès avec {current_model} en {attempt_duration:.2f}s"
                )

            if attempt_duration > 10:
                logger.warning(
                    f"[{operation}] Performance dégradée: {attempt_duration:.2f}s"
                )

            return response

        except concurrent.futures.TimeoutError:
            attempt_duration = time.time() - attempt_start
            logger.error(
                f"[{operation}] TIMEOUT sur {current_model} après {attempt_duration:.2f}s"
            )

            if attempt == len(models_to_try) - 1:
                total_duration = time.time() - start_time
                logger.error(
                    f"[{operation}] Tous les fallbacks échoués après {total_duration:.2f}s (raison=timeout)"
                )
                metric_fallback("mistral", operation_key, attempt)
                metric_fail(
                    "mistral",
                    operation_key,
                    int(total_duration * 1000),
                    reason="timeout",
                )
                return None

            # Timeout → fallback vers modèle suivant (pas de double metric_fallback)
            time.sleep(AI_CONFIG["FALLBACK_BASE_DELAY_S"])
            continue

        except Exception as e:
            error_msg = str(e).lower()
            attempt_duration = time.time() - attempt_start

            status_code, body_text = _extract_status_and_text(e)
            merged_msg = (error_msg + " " + body_text.lower()).strip()

            # On peut tester le modèle suivant
            can_fallback = (
                status_code in AI_CONFIG["ANALYZE_ERROR_STATUS"]
            ) or any(
                k in merged_msg for k in AI_CONFIG["ANALYZE_FALLBACK_KEYWORDS"]
            )

            if can_fallback:
                base = AI_CONFIG.get("CHAT_FALLBACK_BASE_DELAY_S", 0.5)
                maxd = AI_CONFIG.get("CHAT_FALLBACK_MAX_DELAY_S", 3.0)
                wait = min(base * (2**attempt), maxd) + random.uniform(0, 0.3)

                reason = _map_reason_from_msg(merged_msg, status_code)

                log_messages = {
                    "quota": f"[{operation}] QUOTA ATTEINT - {current_model}",
                    "rate_limit": f"[{operation}] RATE LIMIT - {current_model}",
                    "unknown": f"[{operation}] ERREUR INCONNUE - {current_model}",
                }

                logger.warning(
                    log_messages.get(
                        reason, f"[{operation}] Erreur {current_model}: {e}"
                    )
                )

                if attempt == len(models_to_try) - 1:
                    total_duration = time.time() - start_time
                    logger.error(
                        f"[{operation}] Tous les fallbacks échoués après {total_duration:.2f}s (raison={reason})"
                    )

                    metric_fallback("mistral", operation_key, attempt)
                    metric_fail(
                        "mistral",
                        operation_key,
                        int(total_duration * 1000),
                        reason=reason,
                    )
                    return None

                time.sleep(wait)
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

    op_start = time.time()
    response = safe_mistral_call(
        model=AI_CONFIG["EMOTION_MODEL"],
        messages=messages,
        operation="Analyse émotionnelle",
    )

    if response is None:
        metric_fail(
            "mistral",
            "emotion",
            int((time.time() - op_start) * 1000),
            reason="unavailable",
        )
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
                metric_fail(
                    "mistral",
                    "emotion",
                    int((time.time() - op_start) * 1000),
                    reason="bad_format",
                )
                return None, None

        if not isinstance(raw, dict):
            logger.error(f"Format inattendu des émotions (type={type(raw)})")
            metric_fail(
                "mistral",
                "emotion",
                int((time.time() - op_start) * 1000),
                reason="bad_format",
            )
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
            metric_fail(
                "mistral",
                "emotion",
                int((time.time() - op_start) * 1000),
                reason="empty",
            )
            return None, None

        scores = softmax(cleaned)
        dominant = max(scores.items(), key=lambda x: x[1])
        metric_ok("mistral", "emotion", int((time.time() - op_start) * 1000))

        logger.info(f"Émotion dominante: {dominant[0]} ({dominant[1]:.2f})")
        logger.debug(f"Scores détaillés: {json.dumps(scores, indent=2)}")

        return scores, dominant

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Erreur parsing émotions: {e}")
        metric_fail(
            "mistral",
            "emotion",
            int((time.time() - op_start) * 1000),
            reason="bad_json",
        )
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

    op_start = time.time()
    response = safe_mistral_call(
        model=AI_CONFIG["INTERPRETATION_MODEL"],
        messages=messages,
        operation="Interprétation",
    )

    if response is None:
        metric_fail(
            "mistral",
            "interpretation",
            int((time.time() - op_start) * 1000),
            reason="unavailable",
        )
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
            metric_ok(
                "mistral",
                "interpretation",
                int((time.time() - op_start) * 1000),
            )
            logger.info("Interprétation générée avec succès")
            return validated_interpretation
        else:
            metric_fail(
                "mistral",
                "interpretation",
                int((time.time() - op_start) * 1000),
                reason="validation_failed",
            )
            logger.error("Échec validation interprétation")
            return None

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Erreur parsing interprétation: {e}")
        metric_fail(
            "mistral",
            "interpretation",
            int((time.time() - op_start) * 1000),
            reason="bad_json",
        )
        return None


# ---------- GÉNÉRATION D'IMAGES ----------


def generate_image_from_text(user, prompt_text, dream_instance):
    """
    Génère une image IA à partir du texte du rêve, via agent Mistral.
    Stocke l'image en base64 dans le modèle Dream.
    """
    if mistral_client is None:
        logger.error("Génération image impossible - MISTRAL_API_KEY manquante")
        metric_fail("mistral", "image", 0, reason="no_api_key")
        return False

    operation = "image"
    current_model = AI_CONFIG["IMAGE_GENERATION_MODEL"]

    logger.info(f"Génération image pour rêve {dream_instance.id}")
    start_time = time.time()

    system_instructions = read_file("instructions_image.txt")
    max_retries = AI_CONFIG["MAX_ATTEMPTS"]
    backoff_base = AI_CONFIG["BACKOFF_BASE"]
    timeout_s = AI_CONFIG["API_TIMEOUT"]

    total_retry_count = 0
    total_backoff_ms = 0

    for attempt in range(1, max_retries + 1):
        try:
            agent = mistral_client.beta.agents.create(
                model=current_model,
                name="Dream Image Agent",
                instructions=system_instructions,
                tools=[{"type": "image_generation"}],
                completion_args={"temperature": 0.3, "top_p": 0.95},
            )

            # Timeout appliqué
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    mistral_client.beta.conversations.start,
                    agent_id=agent.id,
                    inputs=prompt_text,
                )
                conversation = future.result(timeout=timeout_s)

            # Extraction du fichier généré
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
                reason = "no_file"
                duration = time.time() - start_time
                logger.warning(
                    f"[{operation.upper()}] AUCUN FICHIER RETOURNÉ - {current_model} "
                    f"(tentative {attempt}/{max_retries})"
                )
                metric_fail(
                    "mistral", operation, int(duration * 1000), reason=reason
                )
                return False

            # Télécharger le fichier image
            image_bytes = mistral_client.files.download(file_id=file_id).read()

            dream_instance.set_image_from_bytes(image_bytes, format="PNG")
            dream_instance.save()

            duration = time.time() - start_time
            logger.info(
                f"Image générée avec succès en {duration:.2f}s (tentative {attempt})"
            )

            # Enregistrer les retries une seule fois à la fin
            metric_retry(
                "mistral", operation, total_retry_count, total_backoff_ms
            )
            metric_ok("mistral", operation, int(duration * 1000))
            return True

        except TimeoutError:
            duration = time.time() - start_time
            logger.warning(
                f"[{operation.upper()}] TIMEOUT après {timeout_s}s (tentative {attempt}/{max_retries})"
            )
            if attempt == max_retries:
                logger.error(
                    f"[{operation.upper()}] Échec définitif après {max_retries} tentatives (timeout)"
                )
                metric_retry(
                    "mistral", operation, total_retry_count, total_backoff_ms
                )
                metric_fail(
                    "mistral",
                    operation,
                    int(duration * 1000),
                    reason="timeout",
                )
                return False

        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e).lower()

            # extraire status + body
            status_code, body_text = _extract_status_and_text(e)
            merged_msg = (error_msg + " " + body_text.lower()).strip()

            # mapper la raison centralisée
            reason = _map_reason_from_msg(merged_msg, status_code)

            # décider si retryable
            is_retryable = (
                status_code in AI_CONFIG["ANALYZE_ERROR_STATUS"]
            ) or any(
                k in merged_msg for k in AI_CONFIG["ANALYZE_RETRY_KEYWORDS"]
            )

            if is_retryable and attempt < max_retries:
                sleep_s = min(
                    backoff_base**attempt,
                    AI_CONFIG.get("BACKOFF_MAX_DELAY_S", 5),
                )
                sleep_ms = int(sleep_s * 1000)

                total_retry_count += 1
                total_backoff_ms += sleep_ms

                logger.warning(
                    f"[{operation.upper()}] Erreur {reason} - retry dans {sleep_s:.2f}s "
                    f"(tentative {attempt}/{max_retries}): {e}"
                )
                time.sleep(sleep_s)
                continue
            else:
                logger.error(
                    f"[{operation.upper()}] Erreur définitive {reason} sur {current_model}: {e}"
                )
                metric_retry(
                    "mistral", operation, total_retry_count, total_backoff_ms
                )
                metric_fail(
                    "mistral", operation, int(duration * 1000), reason=reason
                )
                return False

    return False


# ---------- THEMATIQUE ----------


def _preprocess_for_analysis(text: str) -> str:
    """Préprocesse le texte avec spaCy pour analyse thématique"""
    # Import différé des outils NLP
    nlp = get_nlp_model()
    french_stopwords, stemmer = get_nltk_tools()

    if not nlp or not text:
        return _basic_preprocess(text, french_stopwords, stemmer)

    text = text.lower()
    # Préserver plus de ponctuation contextuelle
    text = re.sub(r"[^\w\s\'-]", " ", text)  # Garder apostrophes et tirets

    doc = nlp(text)
    significant_tokens = []

    for token in doc:
        lemma = token.lemma_.lower()
        original = token.text.lower()

        # Critères de sélection plus inclusifs
        keep_token = False

        # Noms et noms propres (priorité)
        if token.pos_ in ["NOUN", "PROPN"]:
            keep_token = True

        # Verbes d'action significatifs (liste étendue)
        elif token.pos_ == "VERB" and lemma in [
            "aller",
            "venir",
            "partir",
            "arriver",
            "entrer",
            "sortir",
            "monter",
            "descendre",
            "voler",
            "tomber",
            "courir",
            "fuir",
            "nager",
            "marcher",
            "conduire",
            "voyager",
            "manger",
            "boire",
            "dormir",
            "rêver",
            "jouer",
            "travailler",
            "étudier",
            "apprendre",
            "rencontrer",
            "voir",
            "regarder",
            "écouter",
            "parler",
            "dire",
            "crier",
            "pleurer",
            "rire",
            "aimer",
            "détester",
            "avoir",
            "être",
            "faire",
            "pouvoir",
            "vouloir",
        ]:
            keep_token = True

        # Adjectifs descriptifs importants
        elif token.pos_ == "ADJ" and len(lemma) >= 4:
            keep_token = True

        # Prépositions et adverbes de lieu/temps utiles
        elif token.pos_ in ["ADP", "ADV"] and original in [
            "chez",
            "dans",
            "sur",
            "sous",
            "avec",
            "sans",
            "pour",
            "par",
            "vers",
            "depuis",
            "hier",
            "aujourd",
            "demain",
            "maintenant",
            "toujours",
            "jamais",
            "souvent",
            "parfois",
            "ici",
            "là",
            "partout",
            "nulle",
            "dehors",
            "dedans",
            "devant",
            "derrière",
        ]:
            keep_token = True

        # Conserver seulement les tokens pertinents
        if keep_token and len(lemma) >= 2:
            # Utiliser lemma pour la cohérence, mais garder original si plus informatif
            final_token = lemma

            # Préférer forme originale pour certains cas
            if (
                original not in french_stopwords
                and original not in DREAM_SPECIFIC_STOPWORDS
                and not original.isdigit()
                and token.is_alpha
                and len(original) >= 3
            ):
                # Garder original si différent du lemma et plus expressif
                if original != lemma and len(original) >= len(lemma):
                    final_token = original

                # Exceptions pour les mots très courts mais importants
                if len(final_token) >= 2 or final_token in [
                    "je",
                    "tu",
                    "il",
                    "on",
                    "me",
                    "te",
                    "se",
                ]:
                    # Dernière vérification d'exclusion
                    exclude_patterns = [
                        "fair",
                        "avoir",
                        "êtr",
                        "etr",
                        "all",
                        "ven",
                    ]
                    if not any(
                        pattern in final_token for pattern in exclude_patterns
                    ):
                        significant_tokens.append(final_token)

    # Déduplication intelligente en gardant la forme la plus informative
    unique_tokens = []
    seen_roots = set()

    for token in significant_tokens:
        # Créer une forme "racine" pour éviter les doublons proches
        root = token[:4] if len(token) > 4 else token

        if root not in seen_roots or len(token) > 4:  # Préférer formes longues
            unique_tokens.append(token)
            seen_roots.add(root)

    return " ".join(unique_tokens)


def _basic_preprocess(text: str, french_stopwords=None, stemmer=None) -> str:
    """Préprocessing basique sans spaCy"""
    if not text:
        return ""

    # Import différé si pas fourni
    if french_stopwords is None or stemmer is None:
        french_stopwords, stemmer = get_nltk_tools()

    text = text.lower()
    text = re.sub(r"[^\w\s\'-]", " ", text)  # Garder apostrophes et tirets
    tokens = text.split()

    filtered = []
    for token in tokens:
        # Critères plus permissifs pour conserver plus de contexte
        if (
            len(token) >= 3  # Réduire le minimum 3 caractères
            and token not in french_stopwords
            and token not in DREAM_SPECIFIC_STOPWORDS
            and not token.isdigit()
            and token.isalpha()
            # Exclusions spécifiques pour éviter les mots parasites
            and token
            not in [
                "avoir",
                "être",
                "etre",
                "faire",
                "aller",
                "venir",
                "dire",
                "voir",
            ]
        ):
            filtered.append(token)

    return " ".join(filtered)


def _bertopic_analysis(dream_texts: List[str], total_dreams: int):
    """Analyse BERTopic pour datasets moyens/grands (8+ rêves)"""
    # Utiliser le modèle BERTopic déjà configuré
    bertopic_model, bertopic_available = get_bertopic_model()

    if not bertopic_available or total_dreams < 8:
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
        # Utiliser directement le modèle configuré
        topics, probabilities = bertopic_model.fit_transform(
            preprocessed_texts
        )

        # Analyser les résultats
        topic_info = bertopic_model.get_topic_info()
        valid_topics = topic_info[topic_info.Topic != -1]

        if len(valid_topics) == 0:
            return None

        # Extraire les thèmes
        theme_results = []
        topic_counts = Counter(topics)

        for topic_id, count in topic_counts.items():
            if topic_id != -1 and count >= 2:
                topic_words = bertopic_model.get_topic(topic_id)
                if topic_words:
                    # Prendre les 2-3 mots les plus représentatifs
                    top_words = [
                        word for word, score in topic_words[:3] if score > 0.1
                    ]
                    if top_words:
                        theme_name = " & ".join(
                            top_words[:2]
                        )  # Maximum 2 mots
                        theme_results.append((theme_name, count))

        return sorted(theme_results, key=lambda x: x[1], reverse=True)

    except Exception as e:
        logger.error(f"Erreur BERTopic: {e}")
        return None


def _category_analysis(dream_texts: List[str], total_dreams: int):
    """Analyse par catégories prédéfinies pour petits datasets"""
    # Import différé des outils NLTK
    french_stopwords, stemmer = get_nltk_tools()

    theme_document_freq = Counter()
    theme_word_freq = Counter()  # Pour compter la fréquence totale des mots

    for dream_text in dream_texts:
        words = _preprocess_for_analysis(dream_text).split()

        # Détecter les catégories présentes dans ce rêve
        detected_categories = set()
        category_word_count = defaultdict(int)

        for category, keywords in THEME_CATEGORIES.items():
            for word in words:
                for keyword in keywords:
                    match_found = False

                    # Différents niveaux de matching
                    if word == keyword:
                        match_found = True
                    elif len(word) >= 4 and len(keyword) >= 4:
                        # Matching partiel pour mots longs
                        if keyword in word or word in keyword:
                            match_found = True
                        # Stemming si disponible
                        elif stemmer and stemmer.stem(word) == stemmer.stem(
                            keyword
                        ):
                            match_found = True

                    if match_found:
                        detected_categories.add(category)
                        category_word_count[category] += 1
                        theme_word_freq[category] += 1
                        break

        # Compter chaque catégorie une fois par rêve (pour document frequency)
        for category in detected_categories:
            theme_document_freq[category] += 1

    # Stratégie adaptative selon le volume de données
    if total_dreams <= 3:
        # Pour très peu de rêves, utiliser la fréquence des mots
        recurring_themes = [
            (theme, freq)
            for theme, freq in theme_word_freq.items()
            if freq >= 1  # Au moins 1 occurrence
        ]
        logger.debug(
            f"Petit dataset ({total_dreams} rêves): utilisation fréquence mots"
        )
    elif total_dreams <= 6:
        # Dataset moyen : mix entre document frequency et word frequency
        recurring_themes = []
        for theme in set(
            list(theme_document_freq.keys()) + list(theme_word_freq.keys())
        ):
            doc_freq = theme_document_freq.get(theme, 0)
            word_freq = theme_word_freq.get(theme, 0)

            # Score hybride : privilégier document frequency mais accepter word frequency élevée
            if doc_freq >= 2:
                score = doc_freq
            elif word_freq >= 2:
                score = 1  # Score minimal mais valide
            else:
                continue

            recurring_themes.append((theme, score))
        logger.debug(
            f"Dataset moyen ({total_dreams} rêves): utilisation score hybride"
        )
    else:
        # Dataset plus grand : utiliser document frequency classique
        recurring_themes = [
            (theme, count)
            for theme, count in theme_document_freq.items()
            if count >= 2
        ]
        logger.debug(
            f"Grand dataset ({total_dreams} rêves): utilisation document frequency"
        )

    # FALLBACK : Si aucun thème trouvé, analyse plus basique
    if not recurring_themes and dream_texts:
        logger.debug(
            "Aucun thème catégorisé trouvé, analyse basique des mots fréquents"
        )

        # Analyser les mots les plus fréquents de tous les rêves
        all_text = " ".join(dream_texts).lower()
        words = _preprocess_for_analysis(all_text).split()

        if words:
            word_counts = Counter(words)
            # Prendre les mots qui apparaissent au moins dans un rêve
            min_freq = max(1, total_dreams // 3) if total_dreams > 3 else 1

            basic_themes = [
                (word, count)
                for word, count in word_counts.most_common(10)
                if count >= min_freq
                and len(word) >= 4
                and word
                not in [
                    "reve",
                    "rever",
                    "fait",
                    "suis",
                    "dans",
                    "avec",
                    "tout",
                    "tres",
                    "bien",
                    "comme",
                    "puis",
                    "alors",
                ]
            ]

            if basic_themes:
                recurring_themes = basic_themes[:5]  # Max 5 thèmes basiques
                logger.debug(
                    f"Thèmes basiques trouvés: {[t[0] for t in recurring_themes]}"
                )

    result = sorted(recurring_themes, key=lambda x: x[1], reverse=True)
    logger.debug(
        f"Analyse catégorique: {len(result)} thèmes pour {total_dreams} rêves"
    )

    return result


def get_themes_stats_filtered(
    user, period=None, start_date=None, end_date=None
):
    """
    Analyse les thématiques récurrentes pour une période donnée
    Utilise la même logique qu'analyze_recurring_themes pour la cohérence
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

    dream_texts = list(dreams.values_list("transcription", flat=True))
    total_dreams = len(dream_texts)

    if total_dreams < 2:
        return {
            "themes": {},
            "total_dreams": total_dreams,
            "top_theme": None,
            "has_data": False,
            "message": "Au moins 2 rêves nécessaires pour détecter des thématiques",
        }

    bertopic_model, bertopic_available = get_bertopic_model()
    if total_dreams >= 8 and bertopic_available:
        themes_results = _bertopic_analysis(dream_texts, total_dreams)
        method = "BERTopic"
    else:
        themes_results = _category_analysis(dream_texts, total_dreams)
        method = "Catégories"

    if not themes_results:
        return {
            "themes": {},
            "total_dreams": total_dreams,
            "top_theme": None,
            "has_data": False,
            "message": "Aucune thématique récurrente détectée",
        }

    # Formatage pour le frontend
    themes_dict = {}
    for theme_name, count in themes_results:
        percentage = round((count / total_dreams) * 100, 1)
        themes_dict[theme_name.capitalize()] = {
            "count": count,
            "percentage": percentage,
        }

    top_theme = {
        "name": themes_results[0][0].capitalize(),
        "count": themes_results[0][1],
        "percentage": round((themes_results[0][1] / total_dreams) * 100, 1),
    }

    logger.info(
        f"Thématiques analysées ({method}): {len(themes_results)} trouvées"
    )

    return {
        "themes": themes_dict,
        "total_dreams": total_dreams,
        "top_theme": top_theme,
        "has_data": True,
        "method": method,
        # Retourner les thèmes bruts pour réutilisation
        "themes_list": [
            theme_name.capitalize() for theme_name, _ in themes_results
        ],
        "raw_themes_results": themes_results,
    }


def get_themes_timeline_filtered(
    user, period=None, start_date=None, end_date=None
):
    """
    Analyse l'évolution des thématiques dans le temps
    NOUVELLE VERSION : Utilise les mêmes thèmes que get_themes_stats_filtered
    """
    logger.info(f"Timeline thématiques user {user.id}")

    dreams_queryset = get_date_filter_queryset(
        user, period, start_date, end_date
    )
    dreams = (
        dreams_queryset.filter(transcription__isnull=False)
        .exclude(transcription="")
        .order_by("created_at")
    )

    if dreams.count() < 2:
        return [], []

    # Obtenir les thèmes globaux de la période (cohérence garantie)
    global_themes_analysis = get_themes_stats_filtered(
        user, period, start_date, end_date
    )

    if not global_themes_analysis["has_data"]:
        return [], []

    # Récupérer la liste des thèmes à suivre dans la timeline
    themes_to_track = global_themes_analysis["themes_list"][
        :10
    ]  # Max 10 thèmes pour lisibilité

    logger.info(f"Thèmes à suivre dans timeline: {themes_to_track}")

    # Grouper les rêves par période temporelle
    if period in ["month", "3months"]:
        # Groupement par semaine pour périodes courtes
        date_format = "%Y-W%U"
        display_format = lambda d: f"Sem {d.strftime('%U')}"
    else:
        # Groupement par mois pour périodes longues
        date_format = "%Y-%m"
        display_format = lambda d: d.strftime("%m/%Y")

    dreams_by_period = defaultdict(list)
    for dream in dreams:
        period_key = dream.created_at.strftime(date_format)
        dreams_by_period[period_key].append(dream.transcription)

    # Pour chaque période, compter SEULEMENT les thèmes prédéfinis
    timeline_data = []

    for period_key in sorted(dreams_by_period.keys()):
        texts = dreams_by_period[period_key]
        period_data = {"period": period_key, "total_dreams": len(texts)}

        # Initialiser tous les thèmes à 0
        for theme in themes_to_track:
            period_data[theme] = 0

        # COMPTER SEULEMENT les thèmes qui correspondent à notre liste globale
        if len(texts) >= 1:
            # Utiliser la même méthode d'analyse que pour les stats globales
            bertopic_model, bertopic_available = get_bertopic_model()
            if len(texts) >= 8 and bertopic_available:
                period_themes = _bertopic_analysis(texts, len(texts))
            else:
                period_themes = _category_analysis(texts, len(texts))

            # Mapper les résultats aux thèmes globaux
            if period_themes:
                for theme_name, count in period_themes:
                    theme_clean = theme_name.capitalize()
                    if theme_clean in themes_to_track:
                        period_data[theme_clean] = count
                        logger.debug(
                            f"Période {period_key}: {theme_clean} = {count}"
                        )

        timeline_data.append(period_data)

    logger.info(
        f"Timeline thématiques: {len(timeline_data)} périodes pour {len(themes_to_track)} thèmes"
    )

    return timeline_data, themes_to_track


def analyze_recurring_themes(user, min_dreams=2, min_occurrence=2):
    """
    Method to analyze the recurring themes in a set of dreams from a single user
    Uses the previously defined algorithm
    """

    logger.info(f"Analyse thématiques récurrentes user {user.id}")

    # Utiliser la fonction harmonisée qui contient déjà toute la logique
    result = get_themes_stats_filtered(user, period="all")

    if not result["has_data"]:
        return {
            "top_theme": "Pas encore de données",
            "percentage": 0,
            "total_dreams": result["total_dreams"],
            "message": result["message"],
        }

    # Reformater pour correspondre à l'ancien format de retour
    top_theme_info = result["top_theme"]

    return {
        "top_theme": top_theme_info["name"],
        "percentage": top_theme_info["percentage"],
        "total_dreams": result["total_dreams"],
        "all_themes": [
            (name, data["count"]) for name, data in result["themes"].items()
        ],
        "message": f"{len(result['themes'])} thématiques trouvées ({result['method']})",
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
    nb_reves = dreams.filter(dream_type="rêve").count()
    nb_cauchemars = dreams.filter(dream_type="cauchemar").count()

    if nb_reves >= nb_cauchemars:
        statut_reveuse = "âme rêveuse"
        pourcentage = round((nb_reves / total) * 100)
        label = "rêves"
    else:
        statut_reveuse = "en proie aux cauchemars"
        pourcentage = round((nb_cauchemars / total) * 100)
        label = "cauchemars"

    # Émotion dominante
    emotions = dreams.values_list("dominant_emotion", flat=True)
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
        "thematique_recurrente": theme_analysis["top_theme"],
        "thematique_percentage": theme_analysis["percentage"],
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
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
            queryset = queryset.filter(created_at__date__range=[start, end])
            logger.debug(f"Filtre personnalisé: {start} à {end}")
            return queryset
        except ValueError:
            logger.warning(f"Dates invalides: {start_date}, {end_date}")
            # En cas d'erreur, on continue avec le period

    # Filtres prédéfinis
    if period == "month":
        start_date = timezone.now() - timedelta(days=30)
        queryset = queryset.filter(created_at__gte=start_date)
        logger.debug("Filtre: 30 derniers jours")
    elif period == "3months":
        start_date = timezone.now() - timedelta(days=90)
        queryset = queryset.filter(created_at__gte=start_date)
        logger.debug("Filtre: 3 derniers mois")
    elif period == "6months":
        start_date = timezone.now() - timedelta(days=180)
        queryset = queryset.filter(created_at__gte=start_date)
        logger.debug("Filtre: 6 derniers mois")
    elif period == "1year":
        start_date = timezone.now() - timedelta(days=365)
        queryset = queryset.filter(created_at__gte=start_date)
        logger.debug("Filtre: 1 an")
    else:
        logger.debug("Aucun filtre appliqué")

    return queryset


def get_dream_type_stats_filtered(
    user, period=None, start_date=None, end_date=None
):
    """
    Method returning the type of the dream (dream, nightmare)
    """

    dreams = get_date_filter_queryset(user, period, start_date, end_date)
    total = dreams.count()

    if total == 0:
        return {
            "percentages": {"rêve": 0, "cauchemar": 0},
            "counts": {"rêve": 0, "cauchemar": 0},
            "total": 0,
        }

    nb_reves = dreams.filter(dream_type="rêve").count()
    nb_cauchemars = dreams.filter(dream_type="cauchemar").count()

    return {
        "percentages": {
            "rêve": round((nb_reves / total) * 100, 1),
            "cauchemar": round((nb_cauchemars / total) * 100, 1),
        },
        "counts": {"rêve": nb_reves, "cauchemar": nb_cauchemars},
        "total": total,
    }


def get_dream_type_timeline_filtered(
    user, period=None, start_date=None, end_date=None
):
    """
    Returns the type of the dream depending on the timeline
    of the recording of said-dream
    """
    dreams = (
        get_date_filter_queryset(user, period, start_date, end_date)
        .annotate(date_only=TruncDate("created_at"))
        .values("date_only", "dream_type")
        .annotate(count=Count("id"))
        .order_by("date_only")
    )

    # Organiser les données par date
    timeline_data = {}
    for dream in dreams:
        date_str = dream["date_only"].strftime("%Y-%m-%d")
        if date_str not in timeline_data:
            timeline_data[date_str] = {"rêve": 0, "cauchemar": 0}
        timeline_data[date_str][dream["dream_type"]] = dream["count"]

    # Convertir en liste pour le frontend
    timeline_list = []
    for date_str, counts in sorted(timeline_data.items()):
        timeline_list.append(
            {
                "date": date_str,
                "rêve": counts["rêve"],
                "cauchemar": counts["cauchemar"],
            }
        )

    return timeline_list


def get_emotions_stats_filtered(
    user, period=None, start_date=None, end_date=None
):
    """
    returns the emotions extracted from the dream with
    the % of dominance of each emotion.
    """
    dreams = get_date_filter_queryset(
        user, period, start_date, end_date
    ).exclude(dominant_emotion__isnull=True)
    total = dreams.count()

    if total == 0:
        return {"percentages": {}, "counts": {}, "total": 0}

    emotion_counts = Counter(dreams.values_list("dominant_emotion", flat=True))

    # Calculer les pourcentages
    emotion_percentages = {}
    for emotion, count in emotion_counts.items():
        emotion_percentages[emotion] = round((count / total) * 100, 1)

    return {
        "percentages": emotion_percentages,
        "counts": dict(emotion_counts),
        "total": total,
    }


def get_emotions_timeline_filtered(
    user, period=None, start_date=None, end_date=None
):
    """
    Returns the statistics of the emotions extracted from the
    dreams during a certain time period.
    """
    dreams = (
        get_date_filter_queryset(user, period, start_date, end_date)
        .exclude(dominant_emotion__isnull=True)
        .annotate(date_only=TruncDate("created_at"))
        .values("date_only", "dominant_emotion")
        .annotate(count=Count("id"))
        .order_by("date_only")
    )

    # Organiser les données par date
    timeline_data = {}
    all_emotions = set()

    for dream in dreams:
        date_str = dream["date_only"].strftime("%Y-%m-%d")
        emotion = dream["dominant_emotion"]
        all_emotions.add(emotion)

        if date_str not in timeline_data:
            timeline_data[date_str] = {}
        timeline_data[date_str][emotion] = dream["count"]

    # Convertir en liste pour le frontend
    timeline_list = []
    for date_str in sorted(timeline_data.keys()):
        entry = {"date": date_str}
        for emotion in all_emotions:
            entry[emotion] = timeline_data[date_str].get(emotion, 0)
        timeline_list.append(entry)

    return timeline_list, list(all_emotions)


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
    """Ex: 'CAUCHEMAR', 'cauchemar', 'Cauchemàr' -> 'Cauchemar'
    (via DREAM_TYPE_LABELS si présent)
    """
    return _normalize_label(val, DREAM_TYPE_LABELS)


def format_interpretation(raw):
    """
    Normalise l'interprétation d'un rêve pour garantir un format stable.
    - raw peut être une string JSON ou déjà un dict
    - Retourne None si l'interprétation est invalide pour déclencher le message générique
    """
    if not raw:
        return None

    # Si c'est une string JSON → parser
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None

    # Si ce n'est pas un dict → rejet
    if not isinstance(raw, dict):
        return None

    # Normalisation via validate_and_fix_interpretation()
    return validate_and_fix_interpretation(raw)
