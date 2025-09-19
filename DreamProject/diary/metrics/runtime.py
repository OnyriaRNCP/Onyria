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
        
        #Métriques avancées
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
        bucket = self.errors.setdefault(key, {})
        final_reason = reason or "unknown"
        bucket[final_reason] = bucket.get(final_reason, 0) + 1


    #Enregistrer durée d'étape pipeline
    def record_pipeline_duration(self, step: str, duration_ms: int) -> None:
        with self._lock:
            self.pipeline_durations.setdefault(step, []).append(int(duration_ms))

    # Enregistrer fallback (logique **par requête** : appeler UNE SEULE fois par requête
    # avec la valeur d'`attempt` finale. Example: attempt=0 (pas de fallback) ; attempt=2 (2 fallbacks effectués))
    def record_fallback(self, provider: str, op: str, attempt: int) -> None:
        key = self._key(provider, op)
        with self._lock:
            bucket = self.fallbacks.setdefault(key, {"fallback_calls": 0})
            # Nombre réel de fallbacks = attempt (si attempt démarre à 0)
            if attempt > 0:
                bucket["fallback_calls"] += attempt




    #Enregistrer retry (peut être appelé par requête ou par opération agrégée)
    def record_retry(self, provider: str, op: str, retry_count: int, backoff_ms: int) -> None:
        key = self._key(provider, op)
        with self._lock:
            bucket = self.retries.setdefault(key, {"total_retries": 0, "backoff_total_ms": 0})
            bucket["total_retries"] += retry_count
            bucket["backoff_total_ms"] += backoff_ms

    #Enregistrer métriques SSE
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
                if not self.sse_metrics[session_id].get("aborted", False):
                    self.sse_metrics[session_id]["completed"] = True

    def record_sse_abort(self, session_id: str) -> None:
        with self._lock:
            if session_id in self.sse_metrics:
                if not self.sse_metrics[session_id].get("completed", False):
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
        Retourne un instantané de la session courante (PROD) ou vide (DEV).
        En DEV, sera entièrement surchargé par les données JSONL.
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
                p99 = _nearest_rank(arr, 99)
                avg = sum(arr) / n
                latency_out[key] = {
                    "count": n,
                    "p50_ms": int(p50),
                    "p95_ms": int(p95),
                    "p99_ms": int(p99),
                    "avg_ms": int(round(avg)),
                }

            # Pipeline durations (sera surchargé en DEV)
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

            # Fallbacks — on ne renvoie **que** fallback_calls
            fallback_out: Dict[str, Dict[str, int]] = {}
            for key, counts in self.fallbacks.items():
                fallback_out[key] = {
                    "fallback_calls": int(counts.get("fallback_calls", 0))
                }

            # Statistiques retry
            retry_out: Dict[str, Dict[str, float]] = {}
            for key, counts in self.retries.items():
                retry_out[key] = {
                    "total_retries": counts.get("total_retries", 0),
                    "backoff_total_ms": counts.get("backoff_total_ms", 0)
                }

            # Métriques SSE (sera surchargé en DEV)
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
                "started_at": int(self.started_at),
                "uptime_s": round(time.time() - self.started_at, 1),
                "availability": availability_out,
                "latency": latency_out,
                "pipeline_durations": pipeline_out,
                "fallbacks": fallback_out,
                "retries": retry_out,
                "sse_quality": sse_out,
                "errors": errors_out,
                "totals": dict(self.totals),
                "last_seen": int(self.last_seen) if self.last_seen else None,
                "notes": "Session data - metrics from current deployment.",
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


def _load_complete_jsonl_snapshot() -> Dict:
    """
    En DEV uniquement : charge TOUTES les métriques depuis les JSONL pour 
    remplacer ENTIÈREMENT les données de session par les données historiques.
    """
    if not (_APP_ENV == "dev" and _PERSIST_TRACES and os.path.exists(_TRACES_PATH)):
        return {}
    
    # Reconstituer availability/latency depuis dev_metrics.jsonl
    availability_data = {}
    latency_data = {}
    errors_data = {}

    # --- Structures pour fallbacks / retries depuis JSONL (logique PAR REQUÊTE) ---
    fallback_data: Dict[str, Dict[str, int]] = {}
    retry_data: Dict[str, Dict[str, int]] = {}

    # 1. Charger dev_metrics.jsonl si disponible
    if os.path.exists(_METRICS_PATH):
        try:
            with open(_METRICS_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    provider = rec.get("provider")
                    op = rec.get("op")
                    status = rec.get("status")
                    latency_ms = rec.get("latency_ms")
                    reason = rec.get("reason")

                    # Gestion des événements fallback / retry (persistés par requête)
                    event = rec.get("event")
                    if provider and op and event == "fallback":
                        key = f"{provider}.{op}"
                        fallback_calls = int(rec.get("fallback_calls", 0))
                        bucket = fallback_data.setdefault(key, {"fallback_calls": 0})
                        if fallback_calls > 0:
                            bucket["fallback_calls"] += fallback_calls
                        continue

                    if provider and op and event == "retry":
                        key = f"{provider}.{op}"
                        rc = int(rec.get("retry_count", 0) or 0)
                        bo = int(rec.get("backoff_ms", 0) or 0)
                        bucket = retry_data.setdefault(key, {"total_retries": 0, "backoff_total_ms": 0})
                        bucket["total_retries"] += rc
                        bucket["backoff_total_ms"] += bo
                        continue

                    if not provider or not op or not status:
                        continue

                    key = f"{provider}.{op}"
                    availability_data.setdefault(key, {"ok": 0, "fail": 0})
                    latency_data.setdefault(key, [])

                    if status == "success":
                        availability_data[key]["ok"] += 1
                    else:
                        availability_data[key]["fail"] += 1
                        if reason:
                            errors_data.setdefault(key, {})
                            errors_data[key][reason] = errors_data[key].get(reason, 0) + 1

                    if latency_ms is not None:
                        latency_data[key].append(int(latency_ms))
        except Exception as e:
            logger.warning(f"Erreur lecture dev_metrics.jsonl: {e}")

    # 2. Charger les données depuis dev_traces.jsonl
    pipeline_data = {}
    sse_sessions = []
    total_dreams = 0
    first_ts = None
    last_ts = None

    try:
        with open(_TRACES_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)

                total_dreams += 1
                ts = rec.get("ts")
                if ts:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts

                # Pipeline durations
                for step_key in ["transcribe_ms", "emotion_ms", "image_ms", "interpretation_ms", "total_duration_ms"]:
                    if rec.get(step_key):
                        final_key = "total_workflow_ms" if step_key == "total_duration_ms" else step_key
                        pipeline_data.setdefault(final_key, []).append(rec[step_key])

                # SSE sessions (1 par rêve)
                sse_sessions.append({
                    "started_at": rec.get("started_at", time.time()),
                    "first_event_at": rec.get("first_event_at") or None,
                    "events_count": rec.get("sse_event_count", 0),
                    "completed": rec.get("sse_completed", True),
                    "aborted": rec.get("sse_aborted", False),
                })


    except Exception as e:
        logger.warning(f"Erreur lecture dev_traces.jsonl: {e}")
        return {}

    # 3. Formater availability avec success_rate
    availability_out = {}
    for key, counts in availability_data.items():
        ok = counts.get("ok", 0)
        fail = counts.get("fail", 0)
        total = ok + fail
        rate = (ok / total) if total else 0.0
        availability_out[key] = {
            "ok": ok,
            "fail": fail,
            "success_rate": round(rate, 3)
        }

    # 4. Formater latency avec percentiles
    latency_out = {}
    for key, values in latency_data.items():
        if not values:
            continue
        arr = sorted(values)
        n = len(arr)
        p50 = _nearest_rank(arr, 50)
        p95 = _nearest_rank(arr, 95)
        p99 = _nearest_rank(arr, 99)
        avg = sum(arr) / n
        latency_out[key] = {
            "count": n,
            "p50_ms": int(p50),
            "p95_ms": int(p95),
            "p99_ms": int(p99),
            "avg_ms": int(round(avg)),
        }

    # 5. Formater pipeline durations
    pipeline_out = {}
    for step, values in pipeline_data.items():
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

    # 6. SSE quality
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
        sse_out["avg_events_per_session"] = round(
            sum(s["events_count"] for s in sse_sessions) / len(sse_sessions), 1
        )

        ttfb_values = []
        for s in sse_sessions:
            if s.get("first_event_at") and s.get("started_at"):
                ttfb_ms = int((s["first_event_at"] - s["started_at"]) * 1000)
                ttfb_values.append(ttfb_ms)
        if ttfb_values:
            sse_out["avg_ttfb_ms"] = int(sum(ttfb_values) / len(ttfb_values))


    # 7. Totaux
    total_ok = sum(counts.get("ok", 0) for counts in availability_data.values())
    total_fail = sum(counts.get("fail", 0) for counts in availability_data.values())
    total_all = total_ok + total_fail

    return {
        "started_at": int(first_ts) if first_ts else int(time.time()),
        "uptime_s": (last_ts - first_ts) if (first_ts and last_ts) else 0,
        "availability": availability_out,
        "latency": latency_out,
        "pipeline_durations": pipeline_out,
        "fallbacks": fallback_data,      
        "retries": retry_data,               # <— agrégé depuis JSONL
        "sse_quality": sse_out,
        "errors": errors_data, 
        "totals": {"ok": total_ok, "fail": total_fail, "all": total_all},
        "last_seen": int(last_ts) if last_ts else None,
        "notes": f"DEV HISTORICAL MODE: ALL metrics from JSONL files. {total_dreams} dreams from {_TRACES_PATH}.",
        "_total_dreams": total_dreams
    }


# NOUVELLES FONCTIONS D'API (identiques)
def metric_pipeline_duration(step: str, duration_ms: int) -> None:
    """Enregistre la durée d'une étape du pipeline"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_pipeline_duration(step, duration_ms)
    logger.info(f"[PIPELINE] step={step} duration_ms={duration_ms}")

def metric_fallback(provider: str, op: str, attempt: int) -> None:
    """
    Enregistre le Fallback **par requête** (APPELER UNE SEULE FOIS PAR REQUÊTE)

    - attempt = 0 → pas de fallback (premier essai)
    - attempt >= 1 → la requête a eu au moins un fallback
    """
    if not _COLLECT_ENABLED:
        return

    # Décaler ici : on interprète attempt=0 comme "pas de fallback"
    _STORE.record_fallback(provider, op, attempt)
    if attempt > 0:
        logger.info(f"[FALLBACK] provider={provider} op={op} fallback={attempt}")
    if _APP_ENV == "dev" and _PERSIST_METRICS:
        _append_jsonl(_METRICS_PATH, {
            "ts": time.time(),
            "provider": provider,
            "op": op,
            "event": "fallback",
            "fallback_calls": attempt,  # nb de vrais fallbacks
        })

def metric_retry(provider: str, op: str, retry_count: int, backoff_ms: int) -> None:
    """Enregistre des statistiques de retry (agrégées ou par requête)"""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_retry(provider, op, retry_count, backoff_ms)
    logger.info(f"[RETRY] provider={provider} op={op} retries={retry_count} backoff_ms={backoff_ms}")
    # Persistance DEV
    if _APP_ENV == "dev" and _PERSIST_METRICS:
        _append_jsonl(_METRICS_PATH, {
            "ts": time.time(),
            "provider": provider,
            "op": op,
            "event": "retry",
            "retry_count": retry_count,
            "backoff_ms": backoff_ms,
        })

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
    """API d'enregistrement de succès."""
    if not _COLLECT_ENABLED:
        return
    _STORE.record_ok(provider, op, latency_ms)
    logger.info(
        "[METRIC] provider=%s op=%s status=success latency_ms=%s",
        provider, op, latency_ms if latency_ms is not None else "0",
    )
    if _APP_ENV == "dev" and _PERSIST_METRICS:
        _append_jsonl(_METRICS_PATH, {
            "ts": time.time(),
            "provider": provider,
            "op": op,
            "status": "success",
            "latency_ms": int(latency_ms) if latency_ms is not None else None,
            "reason": None,
        })

def metric_fail(provider: str, op: str, latency_ms: Optional[int] = None, reason: Optional[str] = None) -> None:
    """API d'enregistrement d'échec."""
    if not _COLLECT_ENABLED:
        return
    final_reason = reason or "unknown"
    _STORE.record_fail(provider, op, latency_ms, final_reason)
    logger.info(
        "[METRIC] provider=%s op=%s status=failed reason=%s latency_ms=%s",
        provider, op, final_reason, latency_ms if latency_ms is not None else "0",
    )
    # --- AJOUT: persistance DEV ---
    if _APP_ENV == "dev" and _PERSIST_METRICS:
        _append_jsonl(_METRICS_PATH, {
            "ts": time.time(),
            "provider": provider,
            "op": op,
            "status": "failed",
            "latency_ms": int(latency_ms) if latency_ms is not None else None,
            "reason": final_reason,
        })

def get_snapshot() -> Dict:
    """
    Retourne un instantané agrégé.
    En DEV: TOUTES les données depuis JSONL (remplace session)
    En PROD: données de session courante uniquement
    """
    if _APP_ENV == "dev" and _PERSIST_TRACES:
        # DEV: remplacer ENTIÈREMENT par données JSONL
        jsonl_snapshot = _load_complete_jsonl_snapshot()
        if jsonl_snapshot:
            return jsonl_snapshot
    
    # PROD ou fallback: snapshot de session
    return _STORE.snapshot()

def calculate_real_dreams_per_day() -> float:
    """
    DEV: depuis TOUTES les dates des traces JSONL
    PROD: depuis session active
    """
    import datetime
    
    if _APP_ENV == "dev" and _PERSIST_TRACES and os.path.exists(_TRACES_PATH):
        # DEV: lire toutes les dates
        dream_dates = []
        try:
            with open(_TRACES_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    created_at = rec.get("created_at")
                    if created_at:
                        date = datetime.datetime.fromtimestamp(created_at).date()
                        dream_dates.append(date)
        except Exception:
            return 0.0
        
        if not dream_dates:
            return 0.0
            
        unique_dates = list(set(dream_dates))
        if len(unique_dates) == 1:
            return len(dream_dates)
        
        min_date = min(unique_dates)
        max_date = max(unique_dates)
        days_span = (max_date - min_date).days + 1
        return len(dream_dates) / days_span
    
    else:
        # PROD: session active
        completed_dreams = _STORE.availability.get("mistral.interpretation", {}).get("ok", 0)
        if completed_dreams == 0:
            return 0.0
        uptime_days = max((time.time() - _STORE.started_at) / 86400, 1/24)
        return completed_dreams / uptime_days

def calculate_business_metrics() -> Dict:
   """
   Calcul unifié des métriques business (DEV et PROD utilisent la même logique)
   """
   PRICING = {
       'groq': {
           # transcription Whisper v3 Turbo
           'transcribe': 0.001   # USD / appel (~1 min audio, sur-estimé)
       },
       'mistral': {
           # analyse émotionnelle (Small)
           'emotion': 0.0003,    # USD / appel (sur-estimé)
           # interprétation du rêve (Large)
           'interpretation': 0.0035,  # USD / appel (sur-estimé)
           # génération d'image (agent image_generation)
           'image': 0.06         # USD / image 
       }
   }

   # 1. Récupérer le nombre de rêves complétés
   if _APP_ENV == "dev" and _PERSIST_TRACES:
       # DEV: depuis JSONL - charger d'abord le snapshot
       snapshot = _load_complete_jsonl_snapshot()
       completed_dreams = snapshot.get("availability", {}).get("mistral.interpretation", {}).get("ok", 0)
       data_source = "JSONL traces"
       availability = snapshot.get("availability", {})
   else:
       # PROD: depuis session active
       completed_dreams = _STORE.availability.get("mistral.interpretation", {}).get("ok", 0)
       data_source = "session metrics"
       availability = _STORE.availability

   # 2. Helper pour récupérer les vrais appels réussis
   def get_ok(provider: str, op: str) -> int:
       return availability.get(f"{provider}.{op}", {}).get("ok", 0)

   # 3. Récupérer les vrais appels par opération
   transcribe_count = get_ok("groq", "transcribe")
   emotion_count = get_ok("mistral", "emotion")
   interpretation_count = get_ok("mistral", "interpretation")

   # 4. Récupérer le nombre réel d'images
   images_count = 0
   if _APP_ENV == "dev" and _PERSIST_TRACES and os.path.exists(_TRACES_PATH):
       try:
           with open(_TRACES_PATH, "r", encoding="utf-8") as f:
               for line in f:
                   line = line.strip()
                   if not line:
                       continue
                   rec = json.loads(line)
                   if rec.get("has_image", False):
                       images_count += 1
       except Exception:
           pass
   else:
       images_count = get_ok("mistral", "image")

   # 5. Calculer les coûts (basé sur les appels réels)
   estimated_cost = (
       transcribe_count * PRICING['groq']['transcribe'] +
       emotion_count * PRICING['mistral']['emotion'] + 
       interpretation_count * PRICING['mistral']['interpretation'] +
       images_count * PRICING['mistral']['image']
   )

   # 6. Calculer la durée de session
   session_duration_hours = 0.0
   if _APP_ENV == "dev" and _PERSIST_TRACES:
       dev_traces = get_dev_traces_summary()
       if dev_traces and dev_traces.get("first_result_at") and dev_traces.get("last_result_at"):
           try:
               import datetime
               first = datetime.datetime.fromisoformat(dev_traces["first_result_at"].replace("Z", "+00:00"))
               last = datetime.datetime.fromisoformat(dev_traces["last_result_at"].replace("Z", "+00:00"))
               session_duration_hours = round((last - first).total_seconds() / 3600, 1)
           except Exception:
               session_duration_hours = 0.0
   else:
       session_duration_hours = round((time.time() - _STORE.started_at) / 3600, 1)

   # 7. Calculer dreams_per_day et cost_per_dream
   dreams_per_day = calculate_real_dreams_per_day()
   cost_per_dream = estimated_cost / completed_dreams if completed_dreams > 0 else 0

   # 8. Notes cohérentes avec la source des données
   notes = (
        f"Costs based on {transcribe_count} transcriptions, "
        f"{emotion_count} emotion analyses, "
        f"{interpretation_count} interpretations, "
        f"and {images_count} images from {data_source}. "
       "Estimates use fixed rates per operation. "
       "More accurate costs require actual audio duration, "
       "token counts (input/output), image parameters, and current API pricing."
   )

   return {
       "dreams_completed": completed_dreams,
       "dreams_per_day": round(dreams_per_day, 2),
       "estimated_cost_usd": round(estimated_cost, 4),
       "cost_per_dream": round(cost_per_dream, 4),
       "session_duration_hours": session_duration_hours,
       "notes": notes
   }


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
    sse_completed: bool = True,
    sse_aborted: bool = False,
    sse_event_count: int = 0,
    first_event_at_ts: Optional[float] = None,
) -> None:
    """Enregistre une trace de rêve en DEV."""
    if not (_APP_ENV == "dev" and _PERSIST_TRACES):
        return
    rec = {
        "ts": time.time(),
        "dream_id": dream_id,
        "user_id": user_id,
        "created_at": float(created_at_ts),
        "dream_type": str(dream_type),
        "dominant_emotion": str(dominant_emotion) if dominant_emotion else "",
        "has_image": bool(has_image),
        "total_duration_ms": int(total_duration_ms),
        "started_at": float(started_at_ts),
        "transcribe_ms": transcribe_ms,
        "emotion_ms": emotion_ms,
        "image_ms": image_ms,
        "interpretation_ms": interpretation_ms,
        "sse_completed": sse_completed,
        "sse_aborted": sse_aborted,
        "sse_event_count": sse_event_count,
        "first_event_at": float(first_event_at_ts) if first_event_at_ts else None,
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
    """Résumé des traces DEV."""
    if not (_APP_ENV == "dev" and _PERSIST_TRACES):
        return None
    try:
        if not os.path.exists(_TRACES_PATH):
            return {"enabled": True, "path": _TRACES_PATH, "total": 0, "first_result_at": None, "last_result_at": None}

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

        return {
            "enabled": True,
            "path": _TRACES_PATH,
            "total": total,
            "first_result_at": _iso_from_ts(first_ts),
            "last_result_at": _iso_from_ts(last_ts),
        }
    except Exception:
        return {"enabled": True, "path": _TRACES_PATH, "total": 0, "first_result_at": None, "last_result_at": None}


def _get_deployment_info() -> Dict:
    """Infos de déploiement Render ou local."""
    deployment_info = {
        "process_start": _iso_from_ts(_STORE.started_at),
        "python_version": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
    }

    # Commit Git (Render injecte RENDER_GIT_COMMIT)
    git_commit = os.getenv("RENDER_GIT_COMMIT") or os.getenv("SOURCE_VERSION")
    if git_commit:
        deployment_info["git_commit"] = git_commit[:7]

    # Version Render
    build_number = os.getenv("RENDER_SERVICE_VERSION")
    if build_number:
        deployment_info["build_number"] = build_number

    # Plateforme
    deployment_info["platform"] = "render" if os.getenv("RENDER") else "local"

    return deployment_info



def get_env_info() -> Dict:
    """Info d'environnement pour /ai/health."""
    deployment_info = _get_deployment_info()
    
    info = {
        "env": _APP_ENV,
        "is_dev": (_APP_ENV == "dev"),
        "mode": "historical" if (_APP_ENV == "dev" and _PERSIST_TRACES) else "session",
        "dev_traces": get_dev_traces_summary() if (_APP_ENV == "dev" and _PERSIST_TRACES) else None,
        "deployment": deployment_info,
    }
    return info


