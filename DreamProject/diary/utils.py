import os
import json
import math
import time
import tempfile
import logging
import httpx
import random
from datetime import datetime, timedelta
from django.utils import timezone
from django.core.files.base import ContentFile
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.conf import settings
from groq import Groq
from mistralai import Mistral
from collections import Counter
from .models import Dream
import unicodedata
from typing import Any, Mapping, Optional
from .constants import EMOTION_LABELS, DREAM_TYPE_LABELS

# --- AJOUT métriques ---
from .metrics.runtime import metric_ok, metric_fail
# -----------------------

# Configuration du logging professionnel
logger = logging.getLogger(__name__)

# Récupération de la configuration centralisée
AI_CONFIG = settings.AI_CONFIG
BASE_DIR = settings.BASE_DIR

# Clients externes avec garde-fou contre les clés manquantes
groq_client = Groq(
    api_key=settings.GROQ_API_KEY,
    http_client=httpx.Client(http2=False, timeout=AI_CONFIG['API_TIMEOUT'])
) if settings.GROQ_API_KEY else None

mistral_client = Mistral(api_key=settings.MISTRAL_API_KEY) if settings.MISTRAL_API_KEY else None

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

    logger.debug(f"Validation interprétation - Clés reçues: {list(interpretation_data.keys())}")

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
                logger.warning(f"Conversion forcée en string pour {key}: {type(value)}")

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
        "connection error", "connection reset", "connection aborted",
        "timeout", "temporarily unavailable", "service unavailable",
        "tls", "ssl", "proxy", "rate limit", "503", "502", "429",
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
            timeout = httpx.Timeout(connect=15.0, read=180.0, write=60.0, pool=60.0)
            limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
            with httpx.Client(http2=False, timeout=timeout, limits=limits, trust_env=True) as client:
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
        logger.error("Échec transcription audio: GROQ_API_KEY manquante ou client non initialisé")
        metric_fail("groq", "transcribe", int((time.time() - start_time) * 1000), reason="no_api_key")
        return None
    
    temp_file_path = None
    last_error = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_file.write(audio_data)
            temp_file_path = temp_file.name

        # Système de retry avec backoff exponentiel et configuration centralisée
        for attempt in range(1, AI_CONFIG['TRANSCRIBE_MAX_RETRIES'] + 1):
            try:
                logger.info(f"Transcription tentative {attempt}/{AI_CONFIG['TRANSCRIBE_MAX_RETRIES']}")
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
                    logger.warning(f"Transcription très courte: {len(transcription.text)} caractères")
                if duration > 5:
                    logger.warning(f"Transcription lente: {duration:.2f}s")
                logger.info(f"Transcription réussie - {len(transcription.text)} caractères en {duration:.2f}s")
                metric_ok("groq", "transcribe", int(duration * 1000))
                return transcription.text

            except Exception as e:
                last_error = e
                if _is_retryable_transcription_error(e) and attempt < AI_CONFIG['TRANSCRIBE_MAX_RETRIES']:
                    sleep_s = round(AI_CONFIG['TRANSCRIBE_BACKOFF_BASE'] ** attempt, 2)
                    logger.warning(f"Transcription erreur réseau (retry dans {sleep_s}s): {e}")
                    time.sleep(sleep_s)
                    continue
                else:
                    logger.error("Transcription error (%s): %s", type(e).__name__, e)
                    break

        # Fallback HTTPX en dernier recours
        logger.info("Tentative fallback HTTPX pour la transcription…")
        result = _transcribe_via_httpx(temp_file_path, language)
        
        if result:
            duration = time.time() - start_time
            logger.info(f"Fallback HTTPX réussi en {duration:.2f}s")
            metric_ok("groq", "transcribe", int(duration * 1000))
            return result
        else:
            duration = time.time() - start_time
            reason = "httpx_fallback_failed"
            if last_error is not None:
                msg = str(last_error).lower()
                if "rate" in msg:
                    reason = "rate_limit"
                elif "quota" in msg:
                    reason = "quota"
                elif "timeout" in msg:
                    reason = "timeout"
            metric_fail("groq", "transcribe", int(duration * 1000), reason=reason)
            return None

    finally:
        # Nettoyage du fichier temporaire même en cas d'erreur
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.warning(f"Impossible de supprimer le fichier temporaire: {e}")

# ---------- SYSTÈME DE FALLBACK ----------

# Concrétisation paramétrable depuis settings.AI_CONFIG (DRY, pas de doublon)
try:
    RETRYABLE_STATUS = set(AI_CONFIG['RETRYABLE_STATUS'])
    RETRYABLE_KEYWORDS = tuple(AI_CONFIG['RETRYABLE_KEYWORDS'])
except KeyError as e:
    logger.error(f"AI_CONFIG manquant: {e}. Règles de retry vides par sécurité.")
    RETRYABLE_STATUS = set()
    RETRYABLE_KEYWORDS = tuple()

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
    Appel Mistral sécurisé avec système de fallback automatique

    Args:
        model: Modèle principal à utiliser
        messages: Messages pour l'API
        operation: Description de l'opération (pour les logs)

    Returns:
        Response de l'API ou None si tous les fallbacks échouent
    """
    if mistral_client is None:
        logger.error(f"[{operation}] Client Mistral non initialisé - MISTRAL_API_KEY manquante")
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
                logger.warning(f"[{operation}] Fallback utilisé: {current_model} en {attempt_duration:.2f}s")
            else:
                logger.info(f"[{operation}] Succès avec {current_model} en {attempt_duration:.2f}s")
            
            # Alerte sur performance dégradée
            if attempt_duration > 10:
                logger.warning(f"[{operation}] Performance dégradée: {attempt_duration:.2f}s")

            return response

        except Exception as e:
            error_msg = str(e).lower()
            attempt_duration = time.time() - attempt_start

            # Erreurs qui nécessitent un fallback
            status_code, body_text = _extract_status_and_text(e)
            merged_msg = (error_msg + " " + body_text.lower()).strip()

            retryable = (
                (status_code in RETRYABLE_STATUS) or
                any(k in merged_msg for k in RETRYABLE_KEYWORDS) or
                any(k in merged_msg for k in [
                    "insufficient_quota",
                    "quota_exceeded",
                    "rate_limit",
                    "model_not_found",
                    "service_unavailable",
                    "timeout",
                ])
            )

            if retryable:
                base = AI_CONFIG.get('CHAT_RETRY_BASE_DELAY_S', 0.5)
                maxd = AI_CONFIG.get('CHAT_RETRY_MAX_DELAY_S', 3.0)
                wait = min(base * (2 ** attempt), maxd) + random.uniform(0, 0.3)

                if "quota" in merged_msg:
                    logger.warning(f"[{operation}] QUOTA ATTEINT - {current_model}")
                elif "rate_limit" in merged_msg or status_code == 429:
                    logger.warning(f"[{operation}] RATE LIMIT - {current_model}")
                else:
                    logger.warning(f"[{operation}] Erreur {current_model}: {e}")

                if attempt == len(models_to_try) - 1:
                    total_duration = time.time() - start_time
                    logger.error(f"[{operation}] Tous les fallbacks échoués après {total_duration:.2f}s")
                    return None

                time.sleep(wait)
                continue
            else:
                logger.error(f"[{operation}] Erreur critique {current_model}: {e}")
                raise e

    return None

# ---------- ANALYSE D'ÉMOTIONS ----------

def analyze_emotions(text):
    """Renvoie le score des émotions + l'émotion dominante avec fallback"""
    logger.info(f"Analyse émotionnelle démarrée - {len(text)} caractères")

    system_prompt = read_file("context_emotion.txt")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    op_start = time.time()
    response = safe_mistral_call(
        model=AI_CONFIG['EMOTION_MODEL'],
        messages=messages,
        operation="Analyse émotionnelle",
    )

    if response is None:
        metric_fail("mistral", "emotion", int((time.time() - op_start) * 1000), reason="unavailable")
        logger.error("Échec analyse émotionnelle - tous les modèles indisponibles")
        return None, None

    try:
        # Contrôle de format robuste
        raw = json.loads(response.choices[0].message.content)

        # Certains modèles peuvent renvoyer une liste de paires; on la convertit en dict si possible
        if isinstance(raw, list):
            try:
                raw = dict(raw)
            except Exception:
                logger.error(f"Format inattendu des émotions (liste non convertible): {raw}")
                metric_fail("mistral", "emotion", int((time.time() - op_start) * 1000), reason="bad_format")
                return None, None

        if not isinstance(raw, dict):
            logger.error(f"Format inattendu des émotions (type={type(raw)})")
            metric_fail("mistral", "emotion", int((time.time() - op_start) * 1000), reason="bad_format")
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
            metric_fail("mistral", "emotion", int((time.time() - op_start) * 1000), reason="empty")
            return None, None

        scores = softmax(cleaned)
        dominant = max(scores.items(), key=lambda x: x[1])
        metric_ok("mistral", "emotion", int((time.time() - op_start) * 1000))

        logger.info(f"Émotion dominante: {dominant[0]} ({dominant[1]:.2f})")
        logger.debug(f"Scores détaillés: {json.dumps(scores, indent=2)}")
        
        return scores, dominant

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Erreur parsing émotions: {e}")
        metric_fail("mistral", "emotion", int((time.time() - op_start) * 1000), reason="bad_json")
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
        model=AI_CONFIG['INTERPRETATION_MODEL'],
        messages=messages,
        operation="Interprétation",
    )

    if response is None:
        metric_fail("mistral", "interpretation", int((time.time() - op_start) * 1000), reason="unavailable")
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
            metric_ok("mistral", "interpretation", int((time.time() - op_start) * 1000))
            logger.info("Interprétation générée avec succès")
            return validated_interpretation
        else:
            metric_fail("mistral", "interpretation", int((time.time() - op_start) * 1000), reason="validation_failed")
            logger.error("Échec validation interprétation")
            return None

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Erreur parsing interprétation: {e}")
        metric_fail("mistral", "interpretation", int((time.time() - op_start) * 1000), reason="bad_json")
        return None

# ---------- GÉNÉRATION D'IMAGES ----------

def generate_image_from_text(user, prompt_text, dream_instance):
    """
    Génère une image IA à partir du texte du rêve, via agent Mistral.
    Stocke l'image en base64 dans le modèle Dream.
    """
    if mistral_client is None:
        logger.error(f"Génération image impossible - MISTRAL_API_KEY manquante")
        metric_fail("mistral", "image", 0, reason="no_api_key")
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
                metric_fail("mistral", "image", int((time.time() - start_time) * 1000), reason="no_file")
                logger.warning("Aucune image générée par l'agent")
                return False

            image_bytes = mistral_client.files.download(file_id=file_id).read()

            # Stocker en base64 au lieu de fichier
            dream_instance.set_image_from_bytes(image_bytes, format='PNG')
            dream_instance.save()

            duration = time.time() - start_time
            logger.info(f"Image générée avec succès en {duration:.2f}s")
            metric_ok("mistral", "image", int(duration * 1000))
            return True

        except Exception as e:
            error_msg = str(e).lower()
            reason = "error"
            if "insufficient_quota" in error_msg or "quota" in error_msg:
                logger.warning(f"Quota image atteint: {e}")
                reason = "quota"
            elif "rate_limit" in error_msg or "too many requests" in error_msg:
                logger.warning(f"Rate limit image: {e}")
                reason = "rate_limit"
            else:
                logger.error(f"Erreur image: {e}")

            metric_fail("mistral", "image", int((time.time() - start_time) * 1000), reason=reason)
            return False

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Erreur génération image après {duration:.2f}s: {e}")
        metric_fail("mistral", "image", int(duration * 1000), reason="exception")
        return False

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
        logger.info(f"Profil calculé: {statut_reveuse} ({pourcentage}%), émotion: {emotion_dominante}")
    else:
        emotion_dominante = "émotion endormie"
        emotion_percentage = 0

    return {
        "statut_reveuse": statut_reveuse,
        "pourcentage_reveuse": pourcentage,
        "label_reveuse": label,
        "emotion_dominante": emotion_dominante,
        "emotion_dominante_percentage": emotion_percentage,
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

def _normalize_label(val: Any, mapping: Optional[Mapping[str, str]] = None) -> str:
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
