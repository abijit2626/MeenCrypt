"""In-memory telemetry for the dashboard's live metrics panels.

Everything here is a rolling window kept in process memory only - nothing
is persisted to disk, and none of it is security-relevant (it is derived
from already-public pipeline timing and fish-activity numbers, never key
material). Restarting the server resets all of it, same as the fish/audio
brokers in server/main.py.

One lock guards all the buffers below, since they're written from both
request-handler threads (encrypt/decrypt) and the fish-poll background
thread (server/main.py's _fish_poll_loop), and read from the metrics
broadcast task on the asyncio event loop.
"""

from __future__ import annotations

import datetime
import threading
from collections import deque
from typing import Literal

_LOCK = threading.Lock()

# Fish activity: one sample per poll tick (server/main.py's VISION_POLL_INTERVAL_S,
# default 1.0s), capped at ~2 minutes of history.
_FISH_SAMPLES_MAXLEN = 120
_fish_samples: deque[dict] = deque(maxlen=_FISH_SAMPLES_MAXLEN)

# Per-op latency, one sample per successful encrypt.
_LATENCY_MAXLEN = 200
_kdf_latencies_ms: deque[float] = deque(maxlen=_LATENCY_MAXLEN)
_wrap_latencies_ms: deque[float] = deque(maxlen=_LATENCY_MAXLEN)

# Raw op timestamps (encrypt/decrypt, success or rejected) - rate is
# derived from these, not stored pre-computed, so the trailing window is
# always exact regardless of how often snapshot() is called.
_OPS_MAXLEN = 2000
_op_events: deque[tuple[float, str]] = deque(maxlen=_OPS_MAXLEN)

# Rate history: one point per broadcast tick (server/main.py's
# METRICS_BROADCAST_INTERVAL_S), so the rate LINES have their own short
# timeline independent of how often an op actually happens.
_RATE_HISTORY_MAXLEN = 60
_encrypt_rate_history: deque[dict] = deque(maxlen=_RATE_HISTORY_MAXLEN)
_decrypt_rate_history: deque[dict] = deque(maxlen=_RATE_HISTORY_MAXLEN)

_OPS_RATE_WINDOW_S = 60.0  # "rate per minute" = count in the trailing 60s


def _now() -> float:
    import time

    return time.time()


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def record_fish_sample(activity_pct: float, fish_count: int, quality: str) -> None:
    """One sample per fish-poll tick - feeds the activity line chart and
    the quality-tier bars (a recent-window count, not all-time)."""
    with _LOCK:
        _fish_samples.append({
            "t": _iso(_now()),
            "activity_pct": round(activity_pct, 2),
            "fish_count": fish_count,
            "quality": quality,
        })


def record_encrypt_latencies(kdf_ms: float | None, wrap_ms: float | None) -> None:
    """wrap_ms is the combined AES-GCM + RSA-OAEP-wrap duration for one
    encrypt operation - a single figure, per the dashboard's "AES+RSA
    wrap latency" panel, not two separate distributions."""
    with _LOCK:
        if kdf_ms is not None:
            _kdf_latencies_ms.append(kdf_ms)
        if wrap_ms is not None:
            _wrap_latencies_ms.append(wrap_ms)


def record_op(kind: Literal["encrypt", "decrypt"], ok: bool) -> None:  # noqa: ARG001 - ok kept for future use/logging
    """Record one encrypt/decrypt attempt (success or rejected - both
    still count toward "how many ops/min are happening")."""
    with _LOCK:
        _op_events.append((_now(), kind))


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile - no numpy dependency."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (pct / 100) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * frac, 3)


def _latency_stats(values: deque[float]) -> dict:
    values_list = list(values)
    return {"p50": _percentile(values_list, 50), "p95": _percentile(values_list, 95), "n": len(values_list)}


def _rate_per_min(kind: str, now: float) -> int:
    cutoff = now - _OPS_RATE_WINDOW_S
    return sum(1 for ts, k in _op_events if k == kind and ts >= cutoff)


def tick_rate_history() -> None:
    """Called once per broadcast tick (see server/main.py's metrics
    broadcast task) to append one point to each rate-history series,
    independent of snapshot() so the line charts have a steady timeline
    even if nobody is polling /api/metrics in between."""
    with _LOCK:
        now = _now()
        point_t = _iso(now)
        _encrypt_rate_history.append({"t": point_t, "rate": _rate_per_min("encrypt", now)})
        _decrypt_rate_history.append({"t": point_t, "rate": _rate_per_min("decrypt", now)})


def snapshot() -> dict:
    with _LOCK:
        now = _now()
        tiers = {"GOOD": 0, "MEDIUM": 0, "BAD": 0}
        for sample in _fish_samples:
            if sample["quality"] in tiers:
                tiers[sample["quality"]] += 1
        return {
            "fish_activity": [
                {"t": s["t"], "activity_pct": s["activity_pct"], "fish_count": s["fish_count"]}
                for s in _fish_samples
            ],
            "quality_tiers": tiers,
            "ops": {
                "encrypt": {
                    "rate_per_min": _rate_per_min("encrypt", now),
                    "history": list(_encrypt_rate_history),
                },
                "decrypt": {
                    "rate_per_min": _rate_per_min("decrypt", now),
                    "history": list(_decrypt_rate_history),
                },
            },
            "latency_ms": {
                "kdf": _latency_stats(_kdf_latencies_ms),
                "wrap": _latency_stats(_wrap_latencies_ms),
            },
        }


def _reset_for_tests() -> None:
    """Test-only: clear every buffer so tests don't see another test's
    (or another poll tick's) samples."""
    with _LOCK:
        _fish_samples.clear()
        _kdf_latencies_ms.clear()
        _wrap_latencies_ms.clear()
        _op_events.clear()
        _encrypt_rate_history.clear()
        _decrypt_rate_history.clear()


__all__ = [
    "record_fish_sample",
    "record_encrypt_latencies",
    "record_op",
    "tick_rate_history",
    "snapshot",
]
