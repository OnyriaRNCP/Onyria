import logging
import os
import time
import threading
from typing import Dict, List, Optional

# Logger dédié au module de métriques
logger = logging.getLogger("metrics")

# APP_ENV vient des variables d'environnement (.env / Render / export APP_ENV=...)
# On désactive la collecte en "test" ou "ci" pour ne pas polluer les chiffres.
_APP_ENV = os.getenv("APP_ENV", "dev").lower()
_COLLECT_ENABLED = _APP_ENV not in ("test", "ci")


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
        # Incrémente les compteurs d’échec + raison (section critique protégée)
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

            # Latence: p50/p95/avg et nombre de points
            latency_out: Dict[str, Dict[str, float]] = {}
            for key, values in self.latency.items():
                if not values:
                    continue
                arr = sorted(values)
                n = len(arr)
                p50 = _nearest_rank(arr, 50)
                p95 = _nearest_rank(arr, 95)
                avg = sum(arr) / n
                latency_out[key] = {
                    "count": n,
                    "p50_ms": int(p50),
                    "p95_ms": int(p95),
                    "avg_ms": int(round(avg)),
                }

            errors_out = {k: dict(v) for k, v in self.errors.items()}

            return {
                "started_at": int(self.started_at),                        # unix s
                "uptime_s": round(time.time() - self.started_at, 1),       # durée
                "availability": availability_out,
                "latency": latency_out,
                "errors": errors_out,
                "totals": dict(self.totals),
                "last_seen": int(self.last_seen) if self.last_seen else None,  # unix s
                "notes": "In-memory since process start; tests/CI ignored.",
            }


# Stockage global en mémoire (durée de vie = process)
_STORE = _Store()


def _nearest_rank(arr: List[int], percentile: int) -> float:
    """
    Percentile "nearest rank" simple qui évite les libs externes.
    Utile ici pour p50/p95 sur un petit volume de points.
    """
    if not arr:
        return 0.0
    n = len(arr)
    rank = max(1, min(n, int((percentile / 100.0) * n + 0.999999)))
    return float(arr[rank - 1])


def metric_ok(provider: str, op: str, latency_ms: Optional[int] = None) -> None:
    """
    API d’enregistrement de succès.
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


def metric_fail(
    provider: str, op: str, latency_ms: Optional[int] = None, reason: Optional[str] = None
) -> None:
    """
    API d’enregistrement d’échec (avec raison si dispo).
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


def get_snapshot() -> Dict:
    """Lecture instantanée, sans effet de bord."""
    return _STORE.snapshot()


class LogMetricsHandler(logging.Handler):
    """
    Handler optionnel : si on le branche sur un logger, il "écoute" les
    lignes contenant [METRIC] et ré-incrémente localement le _STORE.
    Pratique si certaines métriques viennent d’autres modules/process via logs.
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
