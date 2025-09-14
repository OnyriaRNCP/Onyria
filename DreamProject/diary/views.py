import json
import time
from datetime import datetime
import logging
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from .models import Dream
from collections import Counter
from .utils import (
    transcribe_audio,
    analyze_emotions,
    classify_dream,
    interpret_dream,
    generate_image_from_text,
    get_profil_onirique_stats,
    get_dream_type_stats_filtered,
    get_dream_type_timeline_filtered,
    get_emotions_stats_filtered,
    get_emotions_timeline_filtered,
)
from .constants import EMOTION_LABELS, DREAM_TYPE_LABELS, DREAM_ERROR_MESSAGE

logger = logging.getLogger(__name__)

@login_required
def home(request):
    return render(request, 'diary/home.html')

# ---------- Normalisation labels : garantit une CHAÎNE ----------
def _as_str_label(val):
    """
    Garantit un string pour les labels envoyés au frontend/tests.
    - Si liste/tuple -> prend le 1er élément
    - Sinon -> cast en string si besoin
    """
    if isinstance(val, (list, tuple)):
        if not val:
            return ""
        val = val[0]
    return val if isinstance(val, str) else str(val)
# ---------------------------------------------------------------


# ----- Vues principales ----- #


@login_required
def dream_diary_view(request):
    """Journal des rêves"""
    dreams = Dream.objects.filter(user=request.user).order_by('-created_at')

    stats = get_profil_onirique_stats(request.user)

    # Formatage des labels pour l'affichage
    emotion_dominante = stats.get('emotion_dominante')
    if emotion_dominante:
        stats['emotion_dominante'] = EMOTION_LABELS.get(
            emotion_dominante, emotion_dominante.capitalize()
        )

    statut_reveuse = stats.get('statut_reveuse')
    if statut_reveuse:
        stats['statut_reveuse'] = DREAM_TYPE_LABELS.get(
            statut_reveuse, statut_reveuse.capitalize()
        )

    return render(
        request,
        'diary/dream_diary.html',
        {
            'dreams': dreams,
            **stats,  # déstructure les clés du dict `stats` directement dans le contexte
        },
    )


@login_required
def dream_detail_view(request, dream_id):
    """Affiche les détails d'un rêve spécifique"""
    dream = get_object_or_404(Dream, id=dream_id, user=request.user)

    # Formatage des labels pour l'affichage
    if dream.dominant_emotion:
        formatted_dominant_emotion = EMOTION_LABELS.get(
            dream.dominant_emotion, dream.dominant_emotion.capitalize()
        )
        formatted_dream_type = DREAM_TYPE_LABELS.get(
        dream.dream_type, dream.dream_type.capitalize()
        )
    else:
        formatted_dominant_emotion = "Non analysé"
        formatted_dream_type = "Non analysé"

    # Parser l'interprétation si c'est une string JSON
    interpretation = dream.interpretation
    if isinstance(interpretation, str):
        try:
            interpretation = json.loads(interpretation)
        except json.JSONDecodeError:
            interpretation = {}

    context = {
        'dream': dream,
        'formatted_dominant_emotion': formatted_dominant_emotion,
        'formatted_dream_type': formatted_dream_type,
        'interpretation': interpretation,
    }

    return render(request, 'diary/dream_detail.html', context)


@login_required
def dream_recorder_view(request):
    """Page d'enregistrement vocal du rêve"""
    return render(request, 'diary/dream_recorder.html', {
        'DREAM_ERROR_MESSAGE': DREAM_ERROR_MESSAGE
    })


@require_http_methods(["POST"])
@csrf_exempt
def transcribe(request):
    """API : reçoit audio et renvoie texte brut"""
    if 'audio' in request.FILES:
        try:
            audio_file = request.FILES['audio']
            audio_data = audio_file.read()
            logger.info(f"Transcription simple demandée - {len(audio_data)} bytes")
            
            transcription = transcribe_audio(audio_data)
            if transcription:
                logger.info(f"Transcription simple réussie - {len(transcription)} caractères")
                return JsonResponse(
                    {'success': True, 'transcription': transcription}
                )
            else:
                logger.error("Échec transcription simple")
                return JsonResponse(
                    {'success': False, 'error': 'Échec de la transcription'}
                )
        except Exception as e:
            logger.error(f"Erreur transcription simple: {e}")
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Pas de fichier audio'})


@require_http_methods(["POST"])
@login_required
@csrf_exempt
def analyse_from_voice(request):
    """Version SSE (Server-Sent Events) de analyse_from_voice pour affichage progressif des éléments"""
    
    def event_stream():
        start_time = time.time()
        dream = None  # suivi du rêve provisoire pour pouvoir le supprimer en cas d'échec critique
        try:
            if 'audio' not in request.FILES:
                logger.error("Analyse SSE: aucun fichier audio reçu")
                yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"
                return

            audio_file = request.FILES['audio']
            audio_data = audio_file.read()
            logger.info(f"Analyse SSE user {request.user.id} démarrée - {len(audio_data)} bytes")

            # Transcription
            transcription = transcribe_audio(audio_data)
            if not transcription:
                logger.error("Analyse SSE: échec transcription")
                yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"
                return
            yield f"data: {json.dumps({'step': 'transcription', 'data': {'transcription': transcription}})}\n\n"

            # Émotions
            emotions, dominant_emotion = analyze_emotions(transcription)
            if emotions is None:
                logger.error("Analyse SSE: échec analyse émotionnelle")
                yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"
                return
            dream_type = classify_dream(emotions)

            # format "clé brute" (ex: 'joie', 'rêve') -> labels FR
            raw_dominant_key = dominant_emotion[0] if isinstance(dominant_emotion, (list, tuple)) else dominant_emotion
            formatted_dominant_emotion = EMOTION_LABELS.get(raw_dominant_key, str(raw_dominant_key).capitalize())
            formatted_dream_type = DREAM_TYPE_LABELS.get(dream_type, dream_type.capitalize())

            # Normalisation stricte : CHAÎNE (string)
            formatted_dominant_emotion = _as_str_label(formatted_dominant_emotion)
            formatted_dream_type = _as_str_label(formatted_dream_type)

            # Contrat SSE : renvoyer des strings (ex: 'Joie', 'Rêve')
            yield f"data: {json.dumps({'step': 'emotions', 'data': {'dominant_emotion': formatted_dominant_emotion, 'dream_type': formatted_dream_type}})}\n\n"

            # Sauvegarde (créer le rêve d'abord pour avoir l'ID)
            dream = Dream.objects.create(
                user=request.user,
                transcription=transcription,
                emotions=emotions,
                dominant_emotion=raw_dominant_key,
                dream_type=dream_type,
                interpretation={},  # Vide pour l'instant
                is_analyzed=True,
            )
            logger.debug(f"Rêve {dream.id} créé")

            # Image
            image_success = generate_image_from_text(request.user, transcription, dream)
            if image_success:
                dream.refresh_from_db()
                if dream.image_url:
                    logger.info(f"Image envoyée via SSE pour rêve {dream.id}")
                    yield f"data: {json.dumps({'step': 'image', 'data': {'image_path': dream.image_url}})}\n\n"
                else:
                    logger.warning(f"Image générée mais URL manquante pour rêve {dream.id}")
                    yield f"data: {json.dumps({'step': 'image', 'data': {'image_path': None}})}\n\n"
            else:
                logger.warning(f"Échec génération image pour rêve {dream.id}")
                yield f"data: {json.dumps({'step': 'image', 'data': {'image_path': None}})}\n\n"

            # Interprétation
            interpretation = interpret_dream(transcription)
            if interpretation is None:
                logger.error("Analyse SSE: échec interprétation")
                # En cas d'échec critique, ne conserver AUCUN rêve
                try:
                    if dream is not None:
                        dream.delete()
                except Exception:
                    pass
                yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"
                return

            # Mettre à jour le rêve avec l'interprétation
            dream.interpretation = interpretation
            dream.save()

            # Envoyer l'interprétation
            yield f"data: {json.dumps({'step': 'interpretation', 'data': {'interpretation': interpretation}})}\n\n"

            total_duration = time.time() - start_time
            if total_duration > 15:
                logger.warning(f"Analyse SSE lente: {total_duration:.2f}s pour user {request.user.id}")
            
            logger.info(f"Analyse SSE user {request.user.id} réussie - Type: {dream_type}, Émotion: {raw_dominant_key} en {total_duration:.2f}s")
            # Succès explicite pour les tests (image peut échouer sans bloquer)
            yield f"data: {json.dumps({'step': 'complete', 'success': True})}\n\n"

        except Exception as e:
            duration = time.time() - start_time if 'start_time' in locals() else 0
            logger.error(f"Erreur analyse SSE user {request.user.id} après {duration:.2f}s: {e}")
            # Sécurité : si un rêve provisoire existe, le supprimer pour ne rien laisser en cas d'échec global
            try:
                if 'dream' in locals() and dream is not None:
                    dream.delete()
            except Exception:
                pass
            yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    return response


@login_required
def dream_followup(request):
    """Page de suivi des rêves avec statistiques et graphiques + filtres temporels"""

    # Récupération des paramètres de filtre
    period = request.GET.get('period', 'all')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    logger.info(f"Dashboard user {request.user.id} - Period: {period}")

    # Récupération des données avec filtres
    dream_type_stats = get_dream_type_stats_filtered(
        request.user, period, start_date, end_date
    )
    dream_type_timeline = get_dream_type_timeline_filtered(
        request.user, period, start_date, end_date
    )
    emotions_stats = get_emotions_stats_filtered(
        request.user, period, start_date, end_date
    )
    emotions_timeline, emotions_list = get_emotions_timeline_filtered(
        request.user, period, start_date, end_date
    )

    # Formatage des émotions avec les labels français
    formatted_emotions_stats = {}
    if emotions_stats['percentages']:
        for emotion, percentage in emotions_stats['percentages'].items():
            formatted_label = EMOTION_LABELS.get(emotion, emotion.capitalize())
            formatted_emotions_stats[formatted_label] = {
                'percentage': percentage,
                'count': emotions_stats['counts'][emotion],
            }

    # Formatage des émotions pour la timeline
    formatted_emotions_list = []
    for emotion in emotions_list:
        formatted_emotions_list.append(
            {
                'key': emotion,
                'label': EMOTION_LABELS.get(emotion, emotion.capitalize()),
            }
        )

    # Calcul de la plage de dates pour l'affichage
    date_range_info = get_date_range_display(period, start_date, end_date)

    logger.debug(f"Dashboard user {request.user.id} - {dream_type_stats['total']} rêves")

    context = {
        'dream_type_stats': dream_type_stats,
        'dream_type_timeline': dream_type_timeline,
        'emotions_stats': formatted_emotions_stats,
        'emotions_timeline': emotions_timeline,
        'emotions_list': formatted_emotions_list,
        'has_data': dream_type_stats['total'] > 0,
        'current_period': period,
        'current_start_date': start_date,
        'current_end_date': end_date,
        'date_range_display': date_range_info,
    }

    return render(request, 'diary/dream_followup.html', context)


def get_date_range_display(period, start_date=None, end_date=None):
    """Retourne une description lisible de la période sélectionnée"""
    if start_date and end_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').strftime(
                '%d/%m/%Y'
            )
            end = datetime.strptime(end_date, '%Y-%m-%d').strftime('%d/%m/%Y')
            return f"Du {start} au {end}"
        except ValueError:
            return "Période personnalisée"

    period_labels = {
        'month': 'Les 30 derniers jours',
        '3months': 'Les 3 derniers mois',
        '6months': 'Les 6 derniers mois',
        '1year': 'La dernière année',
        'all': 'Toutes les données',
    }

    return period_labels.get(period, 'Toutes les données')


@login_required
def home(request):
    """Page présentant les plans & tarifs"""
    plans = [
        {
            "name": "Freemium",
            "price": "0€",
            "features": [
                "4 rêves par mois",
                "Génération d'image",
            ],
            "target": "Nouveaux utilisateurs curieux",
        },
        {
            "name": "Basic",
            "price": "5.99€/mois",
            "features": [
                "Jusqu'à 8 rêves par mois",
                "Génération d'image",
                "Interprétation symbolique des rêves",
            ],
            "target": "Rêveurs occasionnels",
        },
        {
            "name": "Standard",
            "price": "9.99€/mois",
            "features": [
                "Jusqu'à 30 rêves par mois",
                "Génération d'image",
                "Interprétation symbolique",
                "Profil rêveur",
            ],
            "target": "Rêveurs réguliers",
        },
        {
            "name": "Premium",
            "price": "14.99€/mois",
            "features": [
                "Rêves illimités",
                "Génération d'image",
                "Interprétation symbolique",
                "Profil rêveur",
                "Dashboard de suivi des rêves",
            ],
            "target": "Rêveurs intensifs",
        },
    ]
    return render(request, "diary/home.html", {"plans": plans})
