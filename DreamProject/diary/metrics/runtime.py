import logging
import os
import json
import time
import threading
from typing import Dict, List, Optional

# Logger dédié au module de métriques
logger = logging.getLogger("metrics")

# APP_ENV vient des variables d'environnement (.env / Render / export APP_ENV=...)
# On désactive la collecte en "test" ou "ci" pour ne pas polluer les chiffres.
_APP_ENV = os.getenv("APP_ENV", "dev").lower()
_COLLECT_ENABLED = _APP_ENV not in ("test", "ci")

# --- AJOUT: persistance DEV en JSONL (metrics per-op + traces par rêve) ---
_DEV_DIR = ".dev"
_DEFAULT_METRICS_PATH = os.path.join(_DEV_DIR, "dev_metrics.jsonl")
_DEFAULT_TRACES_PATH = os.path.join(_DEV_DIR, "dev_traces.jsonl")

# Chemins surchargables par variables d'env
_METRICS_PATH = os.getenv("DEV_METRICS_PATH", _DEFAULT_METRICS_PATH)
_TRACES_PATH = os.getenv("DEV_TRACES_PATH", _DEFAULT_TRACES_PATH)

# Comportement: on persiste par défaut en DEV; désactivé ailleurs sauf override explicite
_PERSIST_METRICS = (os.getenv("PERSIST_METRICS", "true").lower() == "true") if _APP_ENV == "dev" else (os.getenv("PERSIST_METRICS", "false").lower() == "true")
_PERSIST_TRACES  = (os.getenv("PERSIST_TRACES",  "true").lower() == "true") if _APP_ENV == "dev" else (os.getenv("PERSIST_TRACES",  "false").lower() == "true")

_MAX_TRACES = 100  # on garde les 100 derniers rêves en DEV
# -------------------------------------------------------------------------


class _Store:
    """
    Petit magasin in-memory thread-safe pour agréger les métriques
    (disponibilité, latence, erreurs). Pas de DB, juste de la mémoire
    de process → remis à zéro à chaque (re)déploiement / redémarrage.
    """
    def __init__(self) -> None:
        self.started_at: float = time.time()          # timestamp unix (secondes)
        self.last_seen: Optional[float] = None        # dernier évènement (unix s)
        self.totals = {"ok": 0, "fail": 0, "all": 0}  # cumul global
        self.availability: Dict[str, Dict[str, int]] = {}  # par clé provider.op
        self.latency: Dict[str, List[int]] = {}            # latences en ms
        self.errors: Dict[str, Dict[str, int]] = {}        # raisons d'échec
        
        # Métriques avancées
        self.fallbacks: Dict[str, Dict[str, int]] = {}     # compteurs fallback
        self.retries: Dict[str, Dict[str, int]] = {}       # compteurs retry
        self.sse_metrics: Dict[str, Dict] = {}             # métriques SSE
        self.pipeline_durations: Dict[str, List[int]] = {} # durées par étape
        
        self._lock = threading.Lock()                      # sérialisation

    @staticmethod
    def _key(provider: str, op: str) -> str:
        # Clé canonique pour regrouper par couple (fournisseur, opération)
        return f"{provider}.{op}"

    def _record_latency(self, key: str, latency_ms: Optional[int]) -> None:
        if latency_ms is None:
            return
        self.latency.setdefault(key, []).append(int(latency_ms))

    def _record_error(self, key: str, reason: Optional[str]) -> None:
        if not reason:
            return
        bucket = self.errors.setdefault(key, {})
        bucket[reason] = bucket.get(reason, 0) + 1

    # Enregistrer durée d'étape pipeline
    def record_pipeline_duration(self, step: str, duration_ms: int) -> None:
        with self._lock:
            self.pipeline_durations.setdefault(step, []).append(int(duration_ms))

    # Enregistrer fallback
    def record_fallback(self, provider: str, op: str, attempt: int) -> None:
        key = self._key(provider, op)
        with self._lock:
            bucket = self.fallbacks.setdefault(key, {"total_calls": 0, "fallback_calls": 0})
            bucket["total_calls"] += 1
            if attempt > 1:
                bucket["fallback_calls"] += 1

    # Enregistrer retry
    def record_retry(self, provider: str, op: str, retry_count: int, backoff_ms: int) -> None:
        key = self._key(provider, op)
        with self._lock:
            bucket = self.retries.setdefault(key, {"total_retries": 0, "backoff_total_ms": 0})
            bucket["total_retries"] += retry_count
            bucket["backoff_total_ms"] += backoff_ms

    # Enregistrer métriques SSE
    def record_sse_start(self, session_id: str) -> None:
        with self._lock:
            self.sse_metrics[session_id] = {
                "started_at": time.time(),
                "first_event_at": None,
                "events_count": 0,
                "completed": False,
                "aborted": False
            }

    def record_sse_first_event(self, session_id: str) -> None:
        with self._lock:
            if session_id in self.sse_metrics:
                self.sse_metrics[session_id]["first_event_at"] = time.time()

    def record_sse_event(self, session_id: str) -> None:
        with self._lock:
            if session_id in self.sse_metrics:
                self.sse_metrics[session_id]["events_count"] += 1

    def record_sse_complete(self, session_id: str) -> None:
        with self._lock:
            if session_id in self.sse_metrics:
                self.sse_metrics[session_id]["completed"] = True

    def record_sse_abort(self, session_id: str) -> None:
        with self._lock:
            if session_id in self.sse_metrics:
                self.sse_metrics[session_id]["aborted"] = True

    def record_ok(self, provider: str, op: str, latency_ms: Optional[int]) -> None:
        # Incrémente les compteurs de succès (section critique protégée)
        key = self._key(provider, op)
        with self._lock:
            self.totals["ok"] += 1
            self.totals["all"] += 1
            self.availability.setdefault(key, {"ok": 0, "fail": 0})["ok"] += 1
            self._record_latency(key, latency_ms)
            self.last_seen = time.time()

    def record_fail(
        self, provider: str, op: str, latency_ms: Optional[int], reason: Optional[str]
    ) -> None:
        # Incrémente les compteurs d'échec + raison (section critique protégée)
        key = self._key(provider, op)
        with self._lock:
            self.totals["fail"] += 1
            self.totals["all"] += 1
            self.availability.setdefault(key, {"ok": 0, "fail": 0})["fail"] += 1
            self._record_latency(key, latency_ms)
            self._record_error(key, reason)
            self.last_seen = time.time()

    def snapshot(self) -> Dict:
        """
        Retourne un instantané agrégé prêt à être sérialisé en JSON.
        Les timestamps sont en secondes (unix). La vue les convertit en ISO.
        """
        with self._lock:
            # Disponibilité + success_rate par clé
            availability_out: Dict[str, Dict[str, float]] = {}
            for key, counts in self.availability.items():
                ok = counts.get("ok", 0)
                fail = counts.get("fail", 0)
                total = ok + fail
                rate = (ok / total) if total else 0.0
                availability_out[key] = {
                    "ok": ok,
                    "fail": fail,
                    "success_rate": round(rate, 3) if total else 0,
                }

            # Latence: p50/p95/p99/avg et nombre de points
            latency_out: Dict[str, Dict[str, float]] = {}
            for key, values in self.latency.items():
                if not values:
                    continue
                arr = sorted(values)
                n = len(arr)
                p50 = _nearest_rank(arr, 50)
                p95 = _nearest_rank(arr, 95)
                p99 = _nearest_rank(arr, 99)  # NOUVEAU
                avg = sum(arr) / n
                latency_out[key] = {
                    "count": n,
                    "p50_ms": int(p50),
                    "p95_ms": int(p95),
                    "p99_ms": int(p99),  # NOUVEAU
                    "avg_ms": int(round(avg)),
                }

            # Durées par étape pipeline
            pipeline_out: Dict[str, Dict[str, float]] = {}
            for step, values in self.pipeline_durations.items():
                if not values:
                    continue
                arr = sorted(values)
                n = len(arr)
                p50 = _nearest_rank(arr, 50)
                p95 = _nearest_rank(arr, 95)
                p99 = _nearest_rank(arr, 99)
                avg = sum(arr) / n
                pipeline_out[step] = {
                    "count": n,
                    "p50_ms": int(p50),
                    "p95_ms": int(p95),
                    "p99_ms": int(p99),
                    "avg_ms": int(round(avg)),
                }

            # Taux de fallback
            fallback_out: Dict[str, Dict[str, float]] = {}
            for key, counts in self.fallbacks.items():
                total = counts.get("total_calls", 0)
                fallback = counts.get("fallback_calls", 0)
                rate = (fallback / total) if total else 0.0
                fallback_out[key] = {
                    "total_calls": total,
                    "fallback_calls": fallback,
                    "fallback_rate": round(rate, 3)
                }

            # Statistiques retry
            retry_out: Dict[str, Dict[str, float]] = {}
            for key, counts in self.retries.items():
                retry_out[key] = {
                    "total_retries": counts.get("total_retries", 0),
                    "backoff_total_ms": counts.get("backoff_total_ms", 0)
                }

            # Métriques SSE agrégées
            sse_sessions = list(self.sse_metrics.values())
            sse_out = {
                "total_sessions": len(sse_sessions),
                "completed_sessions": len([s for s in sse_sessions if s["completed"]]),
                "aborted_sessions": len([s for s in sse_sessions if s["aborted"]]),
                "completion_rate": 0.0,
                "abort_rate": 0.0,
                "avg_ttfb_ms": 0,
                "avg_events_per_session": 0.0
            }
            
            if sse_sessions:
                sse_out["completion_rate"] = round(sse_out["completed_sessions"] / len(sse_sessions), 3)
                sse_out["abort_rate"] = round(sse_out["aborted_sessions"] / len(sse_sessions), 3)
                sse_out["avg_events_per_session"] = round(sum(s["events_count"] for s in sse_sessions) / len(sse_sessions), 1)
                
                # TTFB moyen
                ttfb_values = []
                for s in sse_sessions:
                    if s["first_event_at"] and s["started_at"]:
                        ttfb_ms = int((s["first_event_at"] - s["started_at"]) * 1000)
                        ttfb_values.append(ttfb_ms)
                if ttfb_values:
                    sse_out["avg_ttfb_ms"] = int(sum(ttfb_values) / len(ttfb_values))

            errors_out = {k: dict(v) for k, v in self.errors.items()}

            return {
                "started_at": int(self.started_at),                        # unix s
                "uptime_s": round(time.time() - self.started_at, 1),       # durée
                "availability": availability_out,
                "latency": latency_out,
                "pipeline_durations": pipeline_out,  
                "fallbacks": fallback_out,           
                "retries": retry_out,                
                "sse_quality": sse_out,              
                "errors": errors_out,
                "totals": dict(self.totals),
                "last_seen": int(self.last_seen) if self.last_seen else None,  # unix s
                "notes": (
                    "DEV/DEBUG: In-memory during this Python process only. "
                    "Historical mode enabled in DEV: per-op metrics are rehydrated "
                    f"from {_METRICS_PATH} and per-dream traces from {_TRACES_PATH} across restarts "
                    f"(only the last {_MAX_TRACES} dreams are kept)."
                ),
            }


# Stockage global en mémoire (durée de vie = process)
_STORE = _Store()


def _nearest_rank(arr: List[int], percentile: int) -> float:
    """
    Percentile "nearest rank" simple qui évite les libs externes.
    Utile ici pour p50/p95/p99 sur un petit volume de points.
    """
    if not arr:
        return 0.0
    n = len(arr)
    rank = max(1, min(n, int((percentile / 100.0) * n + 0.999999)))
    return float(arr[rank - 1])


def _ensure_parent_dir(path: str) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    except Exception:
        pass


def _append_jsonl(path: str, obj: dict, max_lines: Optional[int] = None) -> None:
    try:
        _ensure_parent_dir(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        if max_lines is not None:
            # Trim pour ne garder que les N dernières lignes
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > max_lines:
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-max_lines:])
    except Exception:
        # on ne casse pas la collecte si le FS est indisponible
        pass


def _load_metrics_jsonl_into_store() -> None:
    """
    Rejoue les lignes de .dev/dev_metrics.jsonl dans le _STORE pour
    agréger à travers les redémarrages en DEV.
    """
    if not (_APP_ENV == "dev" and _PERSIST_METRICS):
        return
    try:
        if not os.path.exists(_METRICS_PATH):
            return
        with open(_METRICS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                provider = rec.get("provider")
                op = rec.get("op")
                status = rec.get("status")
                latency_ms = rec.get("latency_ms")
                reason = rec.get("reason")
                if not provider or not op or not status:
                    continue
                if status == "success":
                    _STORE.record_ok(provider, op, latency_ms)
                else:
                    _STORE.record_fail(provider, op, latency_ms, reason)
    except Exception:
        # on ignore un éventuel problème de lecture pour ne pas bloquer
        pass


# NOUVELLES FONCTIONS D'API

def metric_pipeline_duration(step: str, duration_ms: int) -> None:
    """Enregistre la durée d'une étape du pipeline"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_pipeline_duration(step, duration_ms)
    logger.info(f"[PIPELINE] step={step} duration_ms={duration_ms}")

def metric_fallback(provider: str, op: str, attempt: int) -> None:
    """Enregistre une tentative avec info de fallback"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_fallback(provider, op, attempt)
    if attempt > 1:
        logger.info(f"[FALLBACK] provider={provider} op={op} attempt={attempt}")

def metric_retry(provider: str, op: str, retry_count: int, backoff_ms: int) -> None:
    """Enregistre des statistiques de retry"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_retry(provider, op, retry_count, backoff_ms)
    logger.info(f"[RETRY] provider={provider} op={op} retries={retry_count} backoff_ms={backoff_ms}")

def metric_sse_start(session_id: str) -> None:
    """Démarre le tracking d'une session SSE"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_sse_start(session_id)

def metric_sse_first_event(session_id: str) -> None:
    """Enregistre le premier événement SSE (TTFB)"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_sse_first_event(session_id)

def metric_sse_event(session_id: str) -> None:
    """Enregistre un événement SSE"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_sse_event(session_id)

def metric_sse_complete(session_id: str) -> None:
    """Marque une session SSE comme complétée"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_sse_complete(session_id)

def metric_sse_abort(session_id: str) -> None:
    """Marque une session SSE comme abandonnée"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_sse_abort(session_id)


def metric_ok(provider: str, op: str, latency_ms: Optional[int] = None) -> None:
    """
    API d'enregistrement de succès.
    No-op si APP_ENV ∈ {test, ci}.
    """
    if not _COLLECT_ENABLED:
        return
    _STORE.record_ok(provider, op, latency_ms)
    # Ligne de log structurée que LogMetricsHandler sait relire si activé
    logger.info(
        "[METRIC] provider=%s op=%s status=success latency_ms=%s",
        provider,
        op,
        latency_ms if latency_ms is not None else "0",
    )
    # --- AJOUT: persistance DEV ---
    if _APP_ENV == "dev" and _PERSIST_METRICS:
        _append_jsonl(_METRICS_PATH, {
            "ts": time.time(),
            "provider": provider,
            "op": op,
            "status": "success",
            "latency_ms": int(latency_ms) if latency_ms is not None else None,
            "reason": None,
        })


def metric_fail(
    provider: str, op: str, latency_ms: Optional[int] = None, reason: Optional[str] = None
) -> None:
    """
    API d'enregistrement d'échec (avec raison si dispo).
    No-op si APP_ENV ∈ {test, ci}.
    """
    if not _COLLECT_ENABLED:
        return
    _STORE.record_fail(provider, op, latency_ms, reason)
    # Même format que ci-dessus, avec status=failed (+ reason)
    logger.info(
        "[METRIC] provider=%s op=%s status=failed reason=%s latency_ms=%s",
        provider,
        op,
        (reason or "unknown"),
        latency_ms if latency_ms is not None else "0",
    )
    # --- AJOUT: persistance DEV ---
    if _APP_ENV == "dev" and _PERSIST_METRICS:
        _append_jsonl(_METRICS_PATH, {
            "ts": time.time(),
            "provider": provider,
            "op": op,
            "status": "failed",
            "latency_ms": int(latency_ms) if latency_ms is not None else None,
            "reason": reason or "unknown",
        })


def get_snapshot() -> Dict:
    """Lecture instantanée, sans effet de bord."""
    return _STORE.snapshot()


class LogMetricsHandler(logging.Handler):
    """
    Handler optionnel : si on le branche sur un logger, il "écoute" les
    lignes contenant [METRIC] et ré-incrémente localement le _STORE.
    Pratique si certaines métriques viennent d'autres modules/process via logs.
    """
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if "[METRIC]" not in msg:
                return
            data = _parse_kv(msg)
            provider = data.get("provider")
            op = data.get("op")
            status = data.get("status")
            latency = _safe_int(data.get("latency_ms"))
            reason = data.get("reason")
            if not provider or not op or not status:
                return
            if status == "success":
                _STORE.record_ok(provider, op, latency)
            else:
                _STORE.record_fail(provider, op, latency, reason)
        except Exception:
            # On évite de casser la chaîne de logs si une ligne est mal formée
            pass


def _parse_kv(line: str) -> Dict[str, str]:
    """
    Parse ultra-simple du segment clef=valeur après le tag [METRIC].
    Exemple: "[METRIC] provider=groq op=transcribe status=success latency_ms=120"
    """
    out: Dict[str, str] = {}
    try:
        segment = line.split("[METRIC]", 1)[1]
    except Exception:
        return out
    tokens = segment.replace(",", " ").strip().split()
    for tok in tokens:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _safe_int(val: Optional[str]) -> Optional[int]:
    """Convertit prudemment une str en int (ou None si vide / invalide)."""
    if val is None:
        return None
    try:
        return int(float(val))
    except Exception:
        return None


# --- AJOUT: traces par rêve (vue produit/métier) ---

def record_dream_trace(
    *,
    dream_id: int,
    user_id: int,
    created_at_ts: float,
    dream_type: str,
    dominant_emotion: str,
    has_image: bool,
    total_duration_ms: int,
    started_at_ts: float,
    transcribe_ms: Optional[int] = None,
    emotion_ms: Optional[int] = None,
    image_ms: Optional[int] = None,
    interpretation_ms: Optional[int] = None,
) -> None:
    """
    Enregistre une ligne "1 rêve = 1 ligne" dans .dev/dev_traces.jsonl (DEV).
    Ne fait rien si pas en DEV ou si persistance désactivée.
    """
    if not (_APP_ENV == "dev" and _PERSIST_TRACES):
        return
    rec = {
        "ts": time.time(),
        "dream_id": dream_id,
        "user_id": user_id,
        "created_at": float(created_at_ts),
        "dream_type": str(dream_type),
        "dominant_emotion": str(dominant_emotion) if dominant_emotion is not None else "",
        "has_image": bool(has_image),
        "total_duration_ms": int(total_duration_ms),
        "started_at": float(started_at_ts),
        "transcribe_ms": transcribe_ms,
        "emotion_ms": emotion_ms,
        "image_ms": image_ms,
        "interpretation_ms": interpretation_ms,
    }
    _append_jsonl(_TRACES_PATH, rec, max_lines=_MAX_TRACES)


def _iso_from_ts(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        import datetime
        return datetime.datetime.fromtimestamp(float(ts), tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def get_dev_traces_summary() -> Optional[Dict]:
    """
    Retourne un résumé des traces DEV (total de rêves + premier/dernier).
    None si non-DEV ou persistance désactivée.
    """
    if not (_APP_ENV == "dev" and _PERSIST_TRACES):
        return None
    try:
        if not os.path.exists(_TRACES_PATH):
            return {
                "enabled": True,
                "path": _TRACES_PATH,
                "total": 0,
                "first_result_at": None,
                "last_result_at": None,
            }

        first_ts = None
        last_ts = None
        total = 0
        with open(_TRACES_PATH, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                except Exception:
                    continue
                t = rec.get("ts")
                if t is None:
                    continue
                total += 1
                if first_ts is None or t < first_ts:
                    first_ts = t
                if last_ts is None or t > last_ts:
                    last_ts = t

        # Le fichier est tronqué aux N dernières lignes ; "total" = nb de rêves considérés.
        return {
            "enabled": True,
            "path": _TRACES_PATH,
            "total": total,
            "first_result_at": _iso_from_ts(first_ts),
            "last_result_at": _iso_from_ts(last_ts),
        }
    except Exception:
        # état minimal en cas de souci de lecture
        return {
            "enabled": True,
            "path": _TRACES_PATH,
            "total": 0,
            "first_result_at": None,
            "last_result_at": None,
        }


def get_env_info() -> Dict:
    """
    Retour d'info d'environnement pour /ai/health (au tout début de la réponse).
    """
    info = {
        "env": _APP_ENV,
        "is_dev": (_APP_ENV == "dev"),
        "mode": "historical" if (_APP_ENV == "dev" and _PERSIST_TRACES) else "session",
        "dev_traces": get_dev_traces_summary() if (_APP_ENV == "dev" and _PERSIST_TRACES) else None,
    }
    return info


# Charger les métriques agrégées depuis .dev/dev_metrics.jsonl (si DEV)
_load_metrics_jsonl_into_store()
