from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.contrib.admin.views.decorators import staff_member_required
from datetime import datetime, timezone

from .runtime import get_snapshot, get_env_info


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
    Endpoint JSON "santé IA". On lit le snapshot in-memory et l'info d'environnement.
    En DEV/historical: on expose le total de rêves depuis .dev/dev_traces.jsonl.
    En PROD/Pré-prod (session): on expose le total de rêves de la session, déduit
    des succès 'mistral.interpretation' (un succès ↔ un rêve complété).
    """
    snap = get_snapshot()
    env = get_env_info()

    # dreams_total :
    dreams_total = 0
    dev_traces = env.get("dev_traces")
    if env.get("mode") == "historical" and dev_traces:
        # DEV: total = nombre de rêves considérés (jusqu'aux 100 derniers)
        dreams_total = dev_traces.get("total", 0)
    else:
        # PROD/Pré-prod: total = nb de rêves complétés dans cette session
        interp = snap.get("availability", {}).get("mistral.interpretation", {})
        dreams_total = int(interp.get("ok", 0))

    data = {
        "environment": env,
        "dreams_total": dreams_total,
        "started_at": _iso(snap.get("started_at")),
        "uptime_s": snap.get("uptime_s"),
        "availability": snap.get("availability"),
        "latency": snap.get("latency"),
        "errors": snap.get("errors"),
        "totals": snap.get("totals"),
        "last_seen": _iso(snap.get("last_seen")),
        "notes": snap.get("notes"),
    }
    return JsonResponse(data)
