import json
from django.http import JsonResponse, HttpResponse
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
    snap = get_snapshot()
    env = get_env_info()
    business_metrics = calculate_business_metrics()

    data = {
        "environment": env,
        "started_at": _iso(snap.get("started_at")),
        "uptime_s": snap.get("uptime_s"),
        "availability": snap.get("availability"),
        "latency": snap.get("latency"),
        
        # MÉTRIQUES TECHNIQUES (100% cohérentes selon source)
        "pipeline_durations": snap.get("pipeline_durations", {}),
        "fallbacks": snap.get("fallbacks", {}),  # Maintenant avec image incluse
        "retries": snap.get("retries", {}),
        "errors": snap.get("errors"),
        "totals": snap.get("totals"),
        "sse_quality": snap.get("sse_quality", {}),
       
        
        # MÉTRIQUES BUSINESS (100% cohérentes avec images incluses)
        "business_metrics": business_metrics,
        
        "last_seen": _iso(snap.get("last_seen")),
        "notes": snap.get("notes"),
    }
    return JsonResponse(data)

@staff_member_required
def ai_health_download(request):
    """Vue pour télécharger les métriques en JSON."""
    data = {
        "env": get_env_info(),
        "metrics": get_snapshot(),
        "business": calculate_business_metrics(),
    }
    json_str = json.dumps(data, indent=2, ensure_ascii=False)

    response = HttpResponse(json_str, content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="ai_metrics.json"'
    return response