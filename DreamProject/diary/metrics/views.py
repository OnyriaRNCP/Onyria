from django.http import JsonResponse
from django.views.decorators.http import require_GET
from datetime import datetime, timezone
from .runtime import get_snapshot

def _iso(ts):
    """
    Convertit un timestamp unix (secondes) en ISO 8601 UTC (ex: 2025-08-30T12:00:00Z).
    None si pas de valeur.
    """
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")

@require_GET
def ai_health_view(request):
    """
    Endpoint JSON "santé IA". On lit le snapshot in-memory puis on
    convertit 'started_at' et 'last_seen' en horodatage ISO 8601.
    """
    data = get_snapshot()
    data["started_at"] = _iso(data.get("started_at"))
    data["last_seen"] = _iso(data.get("last_seen"))
    return JsonResponse(data)
