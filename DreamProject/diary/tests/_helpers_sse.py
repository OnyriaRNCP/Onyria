import json
import logging

__all__ = ["read_sse_events", "sse_to_flat_payload"]

logger = logging.getLogger(__name__)

def read_sse_events(response):
    if hasattr(response, "streaming_content"):
        raw = b"".join(response.streaming_content).decode("utf-8")
    else:
        raw = response.content.decode("utf-8")

    events = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            payload = line[6:]
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                # Log spécifique pour JSON malformé dans les tests
                logger.debug(f"JSON SSE malformé ignoré: {payload[:50]}...")
                continue

    if not events:
        try:
            obj = json.loads(raw)
            events = [
                {
                    "step": "complete" if obj.get("success") else "error",
                    "data": obj,
                }
            ]
        except json.JSONDecodeError as e:
            # Log spécifique au lieu de pass silencieux
            logger.warning(
                f"Impossible de parser la réponse SSE complète: {e}"
            )
        except Exception as e:
            # Log pour autres erreurs inattendues
            logger.error(f"Erreur inattendue lors du parsing SSE: {e}")

    return events


def sse_to_flat_payload(response):
    events = read_sse_events(response)
    out = {
        "success": False,
        "error": None,
        "transcription": None,
        "dominant_emotion": None,
        "dream_type": None,
        "interpretation": None,
        "image_path": None,
    }
    for ev in events:
        step = ev.get("step")
        data = ev.get("data") or {}
        if step == "error":
            out["success"] = False
            out["error"] = (
                data.get("message") or data.get("error") or out["error"]
            )
        elif step == "complete":
            out["success"] = data.get("success", True)
            out["transcription"] = out["transcription"] or data.get(
                "transcription"
            )
            out["dominant_emotion"] = out["dominant_emotion"] or data.get(
                "dominant_emotion"
            )
            out["dream_type"] = out["dream_type"] or data.get("dream_type")
            out["interpretation"] = out["interpretation"] or data.get(
                "interpretation"
            )
            out["image_path"] = out["image_path"] or data.get("image_path")
        elif step == "transcription":
            out["transcription"] = data.get("transcription")
        elif step == "emotions":
            out["dominant_emotion"] = data.get("dominant_emotion")
            out["dream_type"] = data.get("dream_type")
        elif step == "interpretation":
            out["interpretation"] = data.get("interpretation")
        elif step == "image":
            out["image_path"] = data.get("image_path")
    if (
        not any(ev.get("step") == "complete" for ev in events)
        and out["error"] is None
    ):
        out["success"] = out["interpretation"] is not None
    if (
        out["error"] is None
        and not out["success"]
        and out["interpretation"] is not None
    ):
        out["success"] = True
    return out
