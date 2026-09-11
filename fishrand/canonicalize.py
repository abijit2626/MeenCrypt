"""PART 2 - CANONICALIZATION.

The exact same physical dataset must produce the exact same byte
representation before hashing. Arbitrary JSON formatting must never matter
for the cryptographic digest.

Canonical serialization rules (version 1 - v1 samples, v2 vision track):
------------------------------------------------------------------------
1. Fixed, documented field ordering (see KEY ORDER in observe.py for the
   derived metadata; raw observation ordering is below).
2. Deterministic number representation:
   - ints serialize as-is.
   - floats: NaN/infinity are rejected in schema validation; finite floats
     are serialized so that identical physical values map to identical
     bytes (e.g. 1 is "1", 1.0 is "1.0", 3.14 is "3.14").
3. UTF-8, zero unnecessary whitespace.
4. Explicit schema version and source identifier are embedded.
"""

from __future__ import annotations

import json
import math

from .schema import (
    AUDIO_SCHEMA_VERSION,
    AUDIO_SOURCE_IDENTIFIER,
    SCHEMA_VERSION,
    SOURCE_IDENTIFIER,
    TRACK_SCHEMA_VERSION,
)

# Fixed top-level key order for the canonical envelope.
_ORDER_TOP: tuple[str, ...] = ("schema_version", "source", "samples")

# Fixed per-observation key order.
_ORDER_OBS: tuple[str, ...] = ("timestamp_ns", "position", "displacement", "acceleration")

# Fixed per-vector key order.
_ORDER_VEC: tuple[str, ...] = ("x", "y")

_CANONICAL_VERSION_TAG = (
    f'["schema_version",{SCHEMA_VERSION},'
    f'"source","{SOURCE_IDENTIFIER}",'
    '"samples"'
).encode("utf-8")

# v2 vision-track ordering.
_ORDER_TRACK: tuple[str, ...] = ("schema_version", "source", "frames")
_ORDER_FRAME: tuple[str, ...] = ("timestamp", "fish_count", "activity_pct", "fish")
_ORDER_FISH: tuple[str, ...] = ("id", "centroid", "area", "speed", "direction_rad")

_CANONICAL_TRACK_TAG = (
    f'["schema_version",{TRACK_SCHEMA_VERSION},'
    f'"source","{SOURCE_IDENTIFIER}",'
    '"frames"'
).encode("utf-8")

# Audio (ESP32 mic) ordering.
_ORDER_AUDIO: tuple[str, ...] = ("schema_version", "source", "window_duration_s", "readings")
_ORDER_READING: tuple[str, ...] = ("offset_s", "value")

_CANONICAL_AUDIO_TAG = (
    f'["schema_version",{AUDIO_SCHEMA_VERSION},'
    f'"source","{AUDIO_SOURCE_IDENTIFIER}",'
    '"window_duration_s"'
).encode("utf-8")


def canonical_number(value: int | float) -> str:
    """Deterministic string form of an int or finite float.

    1        -> "1"
    1.0      -> "1.0"
    3.14     -> "3.14"
    120.5    -> "120.5"
    """
    if isinstance(value, bool):
        raise TypeError("booleans are not valid canonical numbers")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN/infinity cannot be canonically serialized")
        return repr(value)
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def _vector_bytes(vec: dict) -> bytes:
    return "[" + ",".join(
        f'"{axis}",{canonical_number(vec[axis])}' for axis in _ORDER_VEC
    ) + "]"


def _observation_bytes(sample: dict) -> bytes:
    """Serialize one v1 observation sample with fixed ordering."""
    ts: int = sample["timestamp_ns"]
    pos = _vector_bytes(sample["position"])
    dis = _vector_bytes(sample["displacement"])
    acc = _vector_bytes(sample["acceleration"])
    return (
        '["timestamp_ns",'
        f"{ts},"
        '"position",'
        f"{pos},"
        '"displacement",'
        f"{dis},"
        '"acceleration",'
        f"{acc}]"
    ).encode("utf-8")


def _samples_bytes(samples: list) -> bytes:
    return b"[" + b",".join(_observation_bytes(s) for s in samples) + b"]"


def _canonical_v1_bytes(data: dict) -> bytes:
    """Serialize validated v1 observation data to canonical bytes (version 1)."""
    samples = data["samples"]
    return (
        _CANONICAL_VERSION_TAG
        + b","
        + _samples_bytes(samples)
        + b"]"
    )


def _fish_bytes(fish: dict) -> bytes:
    """Serialize one v2 fish detection with fixed ordering."""
    cx, cy = fish["centroid"]
    return (
        '["id",'
        f"{canonical_number(fish['id'])},"
        '"centroid",'
        f"[{canonical_number(cx)},{canonical_number(cy)}],"
        '"area",'
        f"{canonical_number(fish['area'])},"
        '"speed",'
        f"{canonical_number(fish['speed'])},"
        '"direction_rad",'
        f"{canonical_number(fish['direction_rad'])}]"
    ).encode("utf-8")


def _frame_bytes(frame: dict) -> bytes:
    """Serialize one v2 vision frame with fixed ordering."""
    fish = frame["fish"]
    body = (
        '["timestamp",'
        f"{canonical_number(frame['timestamp'])},"
        '"fish_count",'
        f"{canonical_number(frame['fish_count'])},"
        '"activity_pct",'
        f"{canonical_number(frame['activity_pct'])},"
        '"fish",'
        "["
        + ",".join(_fish_bytes(f).decode("utf-8") for f in fish)
        + "]]"
    )
    return body.encode("utf-8")


def _frames_bytes(frames: list) -> bytes:
    return b"[" + b",".join(_frame_bytes(f) for f in frames) + b"]"


def _canonical_track_bytes(data: dict) -> bytes:
    """Serialize validated v2 vision-track data to canonical bytes (version 2)."""
    frames = data["frames"]
    return (
        _CANONICAL_TRACK_TAG
        + b","
        + _frames_bytes(frames)
        + b"]"
    )


def _reading_bytes(reading: dict) -> bytes:
    """Serialize one audio reading with fixed ordering."""
    return (
        '["offset_s",'
        f"{canonical_number(reading['offset_s'])},"
        '"value",'
        f"{canonical_number(reading['value'])}]"
    ).encode("utf-8")


def _readings_bytes(readings: list) -> bytes:
    return b"[" + b",".join(_reading_bytes(r) for r in readings) + b"]"


def _canonical_audio_bytes(data: dict) -> bytes:
    """Serialize validated audio observation data to canonical bytes."""
    return (
        _CANONICAL_AUDIO_TAG
        + b","
        + canonical_number(data["window_duration_s"]).encode("utf-8")
        + b',"readings",'
        + _readings_bytes(data["readings"])
        + b"]"
    )


def reading_bytes(reading: dict) -> bytes:
    """Canonical bytes for one audio reading (mirrors frame_bytes/sample_bytes).

    The input reading MUST have passed validate_observations().
    """
    return _reading_bytes(reading)


def frame_bytes(frame: dict) -> bytes:
    """Canonical bytes for one v2 vision frame.

    Used by the v3 fish-chain key schedule (one PRF step per frame). The
    input frame MUST have passed validate_observations().
    """
    return _frame_bytes(frame)


def sample_bytes(sample: dict) -> bytes:
    """Canonical bytes for one v1 sample.

    Used by the v3 fish-chain key schedule. The input sample MUST have
    passed validate_observations().
    """
    return _observation_bytes(sample)


def canonical_bytes(data: dict) -> bytes:
    """Serialize validated observation data to canonical bytes.

    Dispatches on shape: `frames` (v2 vision track), `readings` (audio), or
    `samples` (v1). The input MUST have passed validate_observations().
    """
    if "frames" in data:
        return _canonical_track_bytes(data)
    if "readings" in data:
        return _canonical_audio_bytes(data)
    return _canonical_v1_bytes(data)


def canonical_string(data: dict) -> str:
    """Canonical bytes decoded as UTF-8 (for logging/preview only)."""
    return canonical_bytes(data).decode("utf-8")


def json_preview(data: dict, *, indent: int = 2) -> str:
    """Deterministic (sorted-key) JSON preview used *only* for display.

    Not used for hashing - display convenience only.
    """
    return json.dumps(data, indent=indent, sort_keys=False, ensure_ascii=False)