from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.contrib.admin.views.decorators import staff_member_required
from datetime import datetime, timezone

from .runtime import get_snapshot, get_env_info, calculate_business_metrics


def _iso(ts):
    """
    Convertit un timestamp unix (secondes) en ISO 8601 UTC (ex: 2025-08-30T12:00:00Z).
    None si pas de valeur.
    """
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


@staff_member_required  # seulement pour les comptes is_staff=True
@require_GET
def ai_health_view(request):
    """
    Endpoint JSON "santé IA" 100% cohérent.
    DEV: TOUTES les données depuis JSONL (dev_metrics.jsonl + dev_traces.jsonl)
    PROD: TOUTES les données depuis la session active
    """
    snap = get_snapshot()  # Maintenant 100% cohérent selon l'env
    env = get_env_info()

    # dreams_total : cohérent avec les données du snapshot
    dreams_total = 0
    if "_total_dreams" in snap:
        # DEV: nombre depuis JSONL 
        dreams_total = snap["_total_dreams"]
    else:
        # PROD: depuis session
        interp = snap.get("availability", {}).get("mistral.interpretation", {})
        dreams_total = int(interp.get("ok", 0))

    # Métriques business (maintenant 100% cohérentes avec images incluses)
    business_metrics = calculate_business_metrics()

    data = {
        "environment": env,
        "dreams_total": dreams_total,
        "started_at": _iso(snap.get("started_at")),
        "uptime_s": snap.get("uptime_s"),
        "availability": snap.get("availability"),
        "latency": snap.get("latency"),
        
        # MÉTRIQUES TECHNIQUES (100% cohérentes selon source)
        "pipeline_durations": snap.get("pipeline_durations", {}),
        "fallbacks": snap.get("fallbacks", {}),  # Maintenant avec image incluse
        "retries": snap.get("retries", {}),
        "sse_quality": snap.get("sse_quality", {}),
        
        # MÉTRIQUES BUSINESS (100% cohérentes avec images incluses)
        "business_metrics": business_metrics,
        
        "errors": snap.get("errors"),
        "totals": snap.get("totals"),
        "last_seen": _iso(snap.get("last_seen")),
        "notes": snap.get("notes"),
    }
    return JsonResponse(data)