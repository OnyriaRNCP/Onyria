import json
import time
import uuid
from datetime import datetime
import logging
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from django.contrib.auth.decorators import login_required
from django.conf import settings
from .models import Dream
from .utils import (
    analyze_emotions,
    classify_dream,
    interpret_dream,
    generate_image_from_text,
    get_profil_onirique_stats,
    get_dream_type_stats_filtered,
    get_dream_type_timeline_filtered,
    get_emotions_stats_filtered,
    get_emotions_timeline_filtered,
    get_themes_stats_filtered,
    get_themes_timeline_filtered,
    format_emotion_label,
    format_dream_type_label,
    format_interpretation,
    transcribe_audio,
)
from .constants import EMOTION_LABELS, DREAM_ERROR_MESSAGE

logger = logging.getLogger(__name__)

# --- métriques avancées ---
from .metrics.runtime import (
    record_dream_trace, 
    metric_pipeline_duration,
    metric_sse_start, 
    metric_sse_first_event,
    metric_sse_event,
    metric_sse_complete,
    metric_sse_abort
)


# ----- VUES PRINCIPALES ----- #


@login_required
def dream_diary_view(request):
    """Journal des rêves"""
    dreams = Dream.objects.filter(user=request.user).order_by('-created_at')

    stats = get_profil_onirique_stats(request.user)

    # Formatage des labels pour l'affichage
    emotion_dominante = stats.get('emotion_dominante')
    if emotion_dominante:
        stats['emotion_dominante'] = format_emotion_label(emotion_dominante)

    statut_reveuse = stats.get('statut_reveuse')
    if statut_reveuse:
        stats['statut_reveuse'] = format_dream_type_label(statut_reveuse)

    return render(
        request,
        'diary/dream_diary.html',
        {
            'dreams': dreams,
            **stats,  # déstructure les clés du dict `stats` directement dans le contexte
        },
    )


@login_required
@require_POST
def delete_dream(request, dream_id):
    try:
        dream = Dream.objects.get(id=dream_id, user=request.user)
        dream.delete()
        return JsonResponse({'success': True})
    except Dream.DoesNotExist:
        return JsonResponse({'error': 'Rêve introuvable'}, status=404)
    except Exception as e:
        return JsonResponse(
            {'error': 'Erreur lors de la suppression'}, status=500
        )


@login_required
def dream_detail_view(request, dream_id):
    """Affiche les détails d'un rêve spécifique"""
    dream = get_object_or_404(Dream, id=dream_id, user=request.user)

    # Formatage des labels pour l'affichage
    if dream.dominant_emotion:
        formatted_dominant_emotion = format_emotion_label(
            dream.dominant_emotion
        )
        formatted_dream_type = format_dream_type_label(dream.dream_type)
    else:
        formatted_dominant_emotion = "Non analysé"
        formatted_dream_type = "Non analysé"

    # Parser l'interprétation si c'est une string JSON
    interpretation = format_interpretation(dream.interpretation)

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
    return render(
        request,
        'diary/dream_recorder.html',
        {'DREAM_ERROR_MESSAGE': DREAM_ERROR_MESSAGE},
    )


@require_http_methods(["POST"])
@login_required
@csrf_exempt
def analyse_from_voice(request):
    """Version SSE (Server-Sent Events) de analyse_from_voice pour affichage progressif des éléments"""

    def event_stream():
        # ID de session SSE unique pour tracking
        session_id = str(uuid.uuid4())
        metric_sse_start(session_id)
        first_event_sent = False
        first_event_at_ts = None 

        start_time = time.time()
        dream = None  # suivi du rêve provisoire pour pouvoir le supprimer en cas d'échec critique
        aborted = False  # <--- flag ajouté
        event_count = 0  # <--- compteur d'events SSE

        # Variables pour tracking des durées par étape
        step_times = {}

        try:
            if 'audio' not in request.FILES:
                logger.error("Analyse SSE: aucun fichier audio reçu")
                yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"
                metric_sse_abort(session_id)
                aborted = True
                return

            audio_file = request.FILES['audio']
            audio_data = audio_file.read()
            logger.info(
                f"Analyse SSE user {request.user.id} démarrée - {len(audio_data)} bytes"
            )

            # TRANSCRIPTION
            step_times['transcribe_start'] = time.time()
            transcription_result = transcribe_audio(audio_data)
            step_times['transcribe_end'] = time.time()
            transcribe_duration = int((step_times['transcribe_end'] - step_times['transcribe_start']) * 1000)
            metric_pipeline_duration("transcribe_ms", transcribe_duration)

            if not transcription_result:
                logger.error("Analyse SSE: échec transcription")
                yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"
                metric_sse_abort(session_id)
                aborted = True
                return
            
            if isinstance(transcription_result, dict) and "error" in transcription_result:
                error_type = transcription_result["error"]
                logger.warning(f"Analyse SSE: erreur de transcription ({error_type})")
                yield f"data: {json.dumps({'step': error_type, 'message': transcription_result['message']})}\n\n"
                metric_sse_abort(session_id)
                aborted = True
                return
            
            # Si on arrive ici, transcription_result est une string valide
            transcription = transcription_result

            # Premier événement SSE
            if not first_event_sent:
                metric_sse_first_event(session_id)
                first_event_at_ts = time.time()  # <-- ajout
                first_event_sent = True
            metric_sse_event(session_id)
            event_count += 1
            yield f"data: {json.dumps({'step': 'transcription', 'data': {'transcription': transcription}})}\n\n"

            # ÉMOTIONS
            step_times['emotion_start'] = time.time()
            emotions, dominant_emotion = analyze_emotions(transcription)
            step_times['emotion_end'] = time.time()
            emotion_duration = int((step_times['emotion_end'] - step_times['emotion_start']) * 1000)
            metric_pipeline_duration("emotion_ms", emotion_duration)

            if emotions is None:
                logger.error("Analyse SSE: échec analyse émotionnelle")
                yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"
                metric_sse_abort(session_id)
                aborted = True
                return
            dream_type = classify_dream(emotions)

            raw_dominant_key = (
                dominant_emotion[0]
                if isinstance(dominant_emotion, (list, tuple))
                else dominant_emotion
            )
            formatted_dominant_emotion = format_emotion_label(raw_dominant_key)
            formatted_dream_type = format_dream_type_label(dream_type)

            metric_sse_event(session_id)
            event_count += 1
            yield f"data: {json.dumps({'step': 'emotions', 'data': {'dominant_emotion': formatted_dominant_emotion, 'dream_type': formatted_dream_type}})}\n\n"

            # Sauvegarde du rêve
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

            # IMAGE
            step_times['image_start'] = time.time()
            image_success = generate_image_from_text(
                request.user, transcription, dream
            )
            step_times['image_end'] = time.time()
            image_duration = int((step_times['image_end'] - step_times['image_start']) * 1000)
            metric_pipeline_duration("image_ms", image_duration)

            if image_success:
                dream.refresh_from_db()
                if dream.image_url:
                    logger.info(f"Image envoyée via SSE pour rêve {dream.id}")
                    metric_sse_event(session_id)
                    event_count += 1
                    yield f"data: {json.dumps({'step': 'image', 'data': {'image_path': dream.image_url}})}\n\n"
                else:
                    logger.warning(f"Image générée mais URL manquante pour rêve {dream.id}")
                    metric_sse_event(session_id)
                    event_count += 1
                    yield f"data: {json.dumps({'step': 'image', 'data': {'image_path': None}})}\n\n"
            else:
                logger.warning(f"Échec génération image pour rêve {dream.id}")
                metric_sse_event(session_id)
                event_count += 1
                yield f"data: {json.dumps({'step': 'image', 'data': {'image_path': None}})}\n\n"

            # INTERPRÉTATION
            step_times['interpretation_start'] = time.time()
            interpretation = interpret_dream(transcription)
            step_times['interpretation_end'] = time.time()
            interpretation_duration = int((step_times['interpretation_end'] - step_times['interpretation_start']) * 1000)
            metric_pipeline_duration("interpretation_ms", interpretation_duration)

            if interpretation is None:
                logger.error("Analyse SSE: échec interprétation")
                try:
                    if dream is not None:
                        dream.delete()
                except Exception:
                    pass
                yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"
                metric_sse_abort(session_id)
                aborted = True
                return

            interpretation = format_interpretation(interpretation)

            dream.interpretation = interpretation
            dream.save()

            metric_sse_event(session_id)
            event_count += 1
            yield f"data: {json.dumps({'step': 'interpretation', 'data': {'interpretation': interpretation}})}\n\n"


            total_duration = time.time() - start_time
            metric_pipeline_duration("total_workflow_ms", int(total_duration * 1000))

            metric_sse_event(session_id)
            metric_sse_complete(session_id)
            event_count += 1
            yield f"data: {json.dumps({'step': 'complete', 'success': True})}\n\n"

        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, GeneratorExit):
            metric_sse_abort(session_id)
            aborted = True
            return

        except Exception as e:
            duration = time.time() - start_time if 'start_time' in locals() else 0
            logger.error(f"Erreur analyse SSE user {request.user.id} après {duration:.2f}s: {e}")
            try:
                if 'dream' in locals() and dream is not None:
                    dream.delete()
            except Exception:
                pass
            metric_sse_abort(session_id)
            aborted = True
            yield f"data: {json.dumps({'step': 'error', 'message': DREAM_ERROR_MESSAGE})}\n\n"

        finally:
            try:
                record_dream_trace(
                    dream_id=dream.id if dream is not None else -1,  # -1 ou None pour signaler "pas de rêve en DB" si le rêve a échoué
                    user_id=request.user.id,
                    created_at_ts=float(dream.created_at.timestamp()) if (dream and dream.created_at) else time.time(),
                    dream_type=dream.dream_type if dream else "",
                    dominant_emotion=dream.dominant_emotion if dream else "",
                    has_image=bool(getattr(dream, "image_url", None)) if dream else False,
                    total_duration_ms=int((time.time() - start_time) * 1000),
                    started_at_ts=float(start_time),
                    transcribe_ms=step_times.get('transcribe_end', 0) and int((step_times['transcribe_end'] - step_times['transcribe_start']) * 1000),
                    emotion_ms=step_times.get('emotion_end', 0) and int((step_times['emotion_end'] - step_times['emotion_start']) * 1000),
                    image_ms=step_times.get('image_end', 0) and int((step_times['image_end'] - step_times['image_start']) * 1000),
                    interpretation_ms=step_times.get('interpretation_end', 0) and int((step_times['interpretation_end'] - step_times['interpretation_start']) * 1000),
                    sse_completed=not aborted,
                    sse_aborted=aborted,
                    sse_event_count=event_count,
                    first_event_at_ts=first_event_at_ts
                )
            except Exception as e:
                logger.debug(f"Échec record_dream_trace: {e}")


    response = StreamingHttpResponse(
        event_stream(), content_type='text/event-stream'
    )
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
    
    # ✅ HARMONISATION : Récupérer d'abord les stats thématiques (source unique)
    themes_stats = get_themes_stats_filtered(
        request.user, period, start_date, end_date
    )
    
    # ✅ HARMONISATION : Utiliser la liste de thèmes des stats pour la timeline
    if themes_stats['has_data']:
        themes_timeline, themes_list = get_themes_timeline_filtered(
            request.user, period, start_date, end_date
        )
        # S'assurer que themes_list correspond à celui des stats
        themes_list = themes_stats.get('themes_list', themes_list)
        logger.info(f"Thèmes harmonisés: {len(themes_list)} thèmes cohérents entre graphiques")
    else:
        themes_timeline, themes_list = [], []
        logger.info("Pas de données thématiques - graphiques vides")
    
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

    logger.debug(
        f"Dashboard user {request.user.id} - {dream_type_stats['total']} rêves, {len(themes_list)} thèmes harmonisés"
    )

    context = {
        'dream_type_stats': dream_type_stats,
        'dream_type_timeline': dream_type_timeline,
        'emotions_stats': formatted_emotions_stats,
        'emotions_timeline': emotions_timeline,
        'emotions_list': formatted_emotions_list,
        'themes_stats': themes_stats,
        'themes_timeline': themes_timeline,
        'themes_list': themes_list,
        'has_data': dream_type_stats['total'] > 0,
        'current_period': period,
        'current_start_date': start_date,
        'current_end_date': end_date,
        'date_range_display': date_range_info,
        'themes_debug': {
            'method': themes_stats.get('method', 'Aucun'),
            'total_themes_found': len(themes_stats.get('themes', {})),
            'has_timeline_data': len(themes_timeline) > 0,
        } if themes_stats['has_data'] else {}
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