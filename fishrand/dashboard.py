"""DASHBOARD EVENT LOGGING.

Structured pipeline events for the hackathon dashboard. The core crypto
module is decoupled from the transport: api.py yields these event dicts,
the CLI prints them to stdout, and the FastAPI server streams them as SSE.

Event shape:
    {
      "step": "<step_name>",
      "status": "running|ok|error|complete",
      "duration_ms": 123,
      "detail": {...},            # display-only values, never secrets
    }

The AES key is never emitted (see KeyPanel note: KEY MATERIAL: HIDDEN).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable


def make_emit(
    sink: Callable[[dict], Any] | None = None, *, pretty: bool = True
) -> Callable[[str, str, float | None, dict | None], None]:
    """Return an emit(step, status, duration_ms, detail) closure.

    sink=None → pretty printed to stdout; pass a custom sink (e.g. a queue
    feeding SSE) for machine consumption.
    """
    if sink is not None:
        def emit(*args: Any) -> None:
            timestamp_ns = time.time_ns()
            event = to_event(*args, timestamp_ns=timestamp_ns)
            sink(event)
    else:
        def emit(*args: Any) -> None:
            timestamp_ns = time.time_ns()
            event = to_event(*args, timestamp_ns=timestamp_ns)
            if pretty:
                _print_pretty(event)
            else:
                print(json.dumps(event)); sys.stdout.flush()
    return emit


def to_event(
    step: str,
    status: str,
    duration_ms: float | None = None,
    detail: dict | None = None,
    **extra: Any,
) -> dict:
    event: dict = {"step": step, "status": status, "detail": detail or {}}
    if duration_ms is not None:
        event["duration_ms"] = round(duration_ms, 2)
    event.update(extra)
    return event


def _print_pretty(event: dict) -> None:
    step = event["step"].upper().ljust(18)
    status = event["status"].upper()
    icon = {"ok": "  OK", "running": " RUN", "error": "ERR!", "complete": "DONE"}.get(status.lower(), "   ")
    line = f"[{icon}] {step} | {status} "
    if event.get("duration_ms") is not None:
        line += f"| {event['duration_ms']:.1f} ms"
    # Special-case filtering: only render detail lines we want to show, so
    # the derivation stays honest (key hidden).
    print(line)
    detail = event.get("detail") or {}
    if detail:
        for key, value in detail.items():
            if key in ("key_material",):
                print(f"          └─ {key}: HIDDEN")
                continue
            print(f"          └─ {key}: {value}")
    sys.stdout.flush()


__all__ = ["make_emit", "to_event"]