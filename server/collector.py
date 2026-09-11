"""FISHRAND observation collector.

Resolves "current fish observations" from configured sources when the user
asks to encrypt. Sources are tried in order and the first that yields data
validating against the FISHRAND input contract wins.

Source order (env-configurable):
    1. FISHRAND_FISH_URL        live HTTP from the vision engine's service
    2. data/inbox/*.json        files dropped by the vision engine (its
                                fish_log.json — appended NDJSON, one frame
                                per line every 5s). Default: the newest
                                window is FISHRAND_VISION_FRAMES frames.
    3. FISHRAND_FISH_FALLBACK   bundled sample (works out of the box)

The vision engine's raw frame format (each JSON object it logs):
    {"timestamp": ..., "fish_count": ..., "activity_pct": ...,
     "fish": [{"id","centroid","area","speed","direction_rad"}, ...]}
is wrapped into the versioned v2 contract and validated. Malformed data is
rejected with a clear error - never silently substituted.
"""

from __future__ import annotations

import json
import pathlib
import time
import urllib.error
import urllib.request
from typing import Any

from fishrand.schema import SchemaError, validate_observations

from . import config


class FishSourceError(RuntimeError):
    """Raised when no configured source can supply valid observations."""


def _wrap_v2(frames: list) -> dict:
    return {
        "schema_version": 2,
        "source": "fish_vision",
        "frames": frames,
    }


def _wrap_v1_minimal(samples: list) -> dict:
    """Bring the vision team's un-enveloped sample list into the v1 envelope."""
    now_ns = time.time_ns()
    out = []
    for i, sample in enumerate(samples):
        if not isinstance(sample, dict):
            return {}  # let the validator report it
        copy = dict(sample)
        if "timestamp_ns" not in copy:
            copy["timestamp_ns"] = now_ns + i * 100_000_000
        out.append(copy)
    return {
        "schema_version": 1,
        "source": "fish_vision",
        "samples": out,
    }


def _normalize_raw(raw: Any) -> dict | list:
    """Bring vision-engine / raw formats into a versioned envelope.

    Handles:
      - v1 full contract ({schema_version, source, samples})
      - v1 minimal samples-only ({samples:[...]})
      - v2 full contract ({schema_version, source, frames})
      - a bare frames list ([frame, ...])
      - a single raw vision frame ({timestamp, fish_count, activity_pct, fish})
    Anything else is returned as-is so validate_observations produces the
    authoritative error.
    """
    if isinstance(raw, dict):
        if "samples" in raw:
            if "schema_version" not in raw:
                return _wrap_v1_minimal(raw["samples"])
            return raw
        if "frames" in raw:
            if "schema_version" not in raw:
                return _wrap_v2(raw["frames"])
            return raw
        if "fish" in raw and "timestamp" in raw:
            return _wrap_v2([raw])
        return raw
    if isinstance(raw, list):
        return _wrap_v2(raw)
    return raw


def _validate(raw: Any, source: str) -> dict:
    return validate_observations(_normalize_raw(raw))


def _read_documents(text: str) -> list[tuple[Any, int]]:
    """Parse a log file as either one JSON document or NDJSON lines.

    Returns a list of (doc, line_number); docs are the raw values of each
    parseable record. A single JSON dict/list file counts as one document.
    """
    stripped = text.strip()
    if not stripped:
        return []
    try:
        doc = json.loads(stripped)
        return [(doc, 0)]
    except json.JSONDecodeError:
        pass
    docs: list[tuple[Any, int]] = []
    for lineno, line in enumerate(stripped.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            docs.append((json.loads(line), lineno))
        except json.JSONDecodeError:
            continue
    return docs


def _window_from_log(text: str, max_frames: int) -> tuple[dict | None, str | None]:
    """Build the newest observation window from a (perhaps NDJSON) log.

    Returns (raw_enveloped_dict, kind) where kind is 'v1' or 'v2'. None if
    nothing parseable.
    """
    docs = _read_documents(text)
    if not docs:
        return None, None

    # A single versioned contract window is passed through untouched.
    if len(docs) == 1:
        doc = docs[0][0]
        if isinstance(doc, dict):
            if "samples" in doc:
                return doc, "v1"
            if "frames" in doc:
                return doc, "v2"

    # Vision engine logs: bundle the latest max_frames frame records.
    frames: list = []
    for doc, _ in docs:
        if isinstance(doc, list):
            frames.extend(doc)
        elif isinstance(doc, dict) and "frames" in doc:
            frames.extend(doc["frames"])
        elif isinstance(doc, dict) and "fish" in doc:
            frames.append(doc)
    if not frames:
        return None, None
    return _wrap_v2(frames[-max_frames:]), "v2"


def _load_fallback() -> tuple[dict, str]:
    path = pathlib.Path(config.DEFAULT_FALLBACK)
    if not path.exists():
        raise FishSourceError(f"fallback observations file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FishSourceError(f"fallback {path}: invalid JSON: {exc}") from exc
    return _validate(raw, str(path)), f"fallback:{path.name}"


def _eat_inbox() -> tuple[dict, str] | None:
    if not config.WATCH_INBOX:
        return None
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(config.INBOX_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    newest = files[0]
    try:
        text = newest.read_text(encoding="utf-8")
    except OSError as exc:
        raise FishSourceError(f"inbox {newest.name}: unreadable file: {exc}") from exc
    envelope, kind = _window_from_log(text, config.VISION_FRAMES)
    if envelope is None:
        raise FishSourceError(f"inbox {newest.name}: no parseable JSON")
    label = f"inbox:{newest.name}"
    return _validate(envelope, label), label
    # keep kind param for clarity of the log source; not surfaced yet.


def _fetch_url() -> tuple[dict, str]:
    """Fetch + validate observations from the vision engine's service."""
    if not config.FISH_URL:
        raise FishSourceError("FISHRAND_FISH_URL is not configured")
    try:
        with urllib.request.urlopen(config.FISH_URL, timeout=3) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise FishSourceError(f"FISHRAND_FISH_URL unreachable: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise FishSourceError(f"FISHRAND_FISH_URL returned invalid JSON: {exc}") from exc
    return _validate(raw, config.FISH_URL), f"live:{config.FISH_URL}"


def collect_fish_verbose() -> tuple[dict, str]:
    """Return (validated fish observations, the source label ACTUALLY used).

    Tries live URL first, then inbox (if enabled), then the bundled
    fallback — falling through whenever a configured source is empty or
    unreachable. Unlike current_source() (which only reflects which
    sources are *configured*), the label returned here always matches the
    data that comes back, even when a fall-through happened. Callers that
    persist a fish_source into a package's metadata should use this
    instead of collect_fish() + current_source() separately, since those
    two calls can otherwise disagree about what was actually collected.
    """
    if config.FISH_URL:
        try:
            return _fetch_url()
        except (FishSourceError, SchemaError):
            pass

    inbox_result = _eat_inbox()
    if inbox_result is not None:
        return inbox_result

    return _load_fallback()


def collect_fish() -> dict:
    """Return the current validated fish observations from configured sources.

    Raises FishSourceError when every source fails. Tries live URL first,
    then inbox (if enabled), then the bundled fallback.
    """
    return collect_fish_verbose()[0]


def current_source() -> str:
    """Best-effort source label based on CONFIGURATION alone (cheap, no I/O).

    This does NOT guarantee a matching collect_fish() call used this exact
    source — a configured source can be empty/unreachable and cause a
    fall-through. It's fine for a quick health-check display; anything
    that needs to record which source a specific encryption actually used
    (e.g. package metadata) must use collect_fish_verbose() instead.
    """
    if config.FISH_URL:
        return f"live:{config.FISH_URL}"
    if config.WATCH_INBOX:
        return "inbox:data/inbox"
    return f"fallback:{pathlib.Path(config.DEFAULT_FALLBACK).name}"


__all__ = [
    "FishSourceError",
    "collect_fish",
    "collect_fish_verbose",
    "current_source",
    "_normalize_raw",
    "_window_from_log",
]