"""PART 1 - FISHRAND INPUT CONTRACT.

Strict validation for fish observation JSON. The JSON is treated as
completely untrusted input. Nothing gets executed, no arbitrary structures
are accepted, and only well-formed observation data passes through.

Two accepted shapes (validated against the same contract):

  v1 (SAMPLES)  schema_version 1 — per-sample motion vectors emitted by the
                classic collector: {samples:[timestamp_ns, position,
                displacement, acceleration]}.

  v2 (VISION-TRACK / FRAMES)  schema_version 2 — the raw output of the
                vision engine (abijit2626/MeenCrypt vision.py): a sequence of
                `frames`, each a camera snapshot with the detected fish
                {timestamp, fish_count, activity_pct, fish:[{id, centroid,
                area, speed, direction_rad}]}.

  AUDIO (READINGS)  independent AUDIO_SCHEMA_VERSION — one window of ESP32 +
                INMP441 Sound_Level readings read over USB serial (see
                server/audio_serial.py): {schema_version, source,
                window_duration_s, readings:[{offset_s, value}]}.

Public interface (matching the spec):

    load_observations(path)
        ↓
    validate_observations(data)
        ↓
    canonicalize_observations(data)
"""

from __future__ import annotations

import copy
import json
import math
import pathlib
from typing import Any

# v1: per-sample motion observations.
SCHEMA_VERSION: int = 1
# v2: vision-track frames (friend's vision.py output).
TRACK_SCHEMA_VERSION: int = 2
SOURCE_IDENTIFIER: str = "fish_vision"

# v3 (audio): ESP32 + INMP441 Sound_Level readings, one logical window.
# Independent version space from the fish SCHEMA_VERSION/TRACK_SCHEMA_VERSION
# above - this counts audio schema revisions, not fish ones.
AUDIO_SCHEMA_VERSION: int = 1
AUDIO_SOURCE_IDENTIFIER: str = "esp32_mic"

MAX_SAMPLES: int = 10_000
MIN_SAMPLES: int = 1

# Track frame bounds: a window is a list of camera snapshots; each snapshot
# tracks at most MAX_FISH simultaneous fish.
MAX_FRAMES: int = 2_000
MAX_FISH: int = 64

# fish.id is an opaque, ever-incrementing per-session track label assigned
# by the vision engine's tracker (see vision.py FishTracker.next_id) - NOT
# an index into the per-frame fish list, so it must NOT be bounded by
# MAX_FISH. A real session reassigns ids often (occlusion/noise/ID
# thrashing), so ids climb into the hundreds or thousands within minutes.
FISH_ID_MAX: int = 1_000_000_000

# Coordinate bounds: reasonable pixels for a camera view of a fish tank.
POSITION_MIN: float = -100_000.0
POSITION_MAX: float = 100_000.0
DISPLACEMENT_LIMIT: float = 10_000.0
ACCELERATION_LIMIT: float = 10_000.0

# Human-reasonable timestamp range that also rejects impossible input.
# Upper bound ~ year 2525, far future; lower bound ~ year 1970 (epoch 0).
TIMESTAMP_NS_MIN: int = 0
TIMESTAMP_NS_MAX: int = 17_576_000_000_000_000_000  # 2525-01-01 in ns

# v2 frame bounds (epoch-seconds timestamps, vision metrics).
TIMESTAMP_SEC_MIN: float = 0.0
TIMESTAMP_SEC_MAX: float = 2_000_000_000.0  # ~ year 2033
CENTROID_LIMIT: float = 100_000.0
AREA_LIMIT: float = 1_000_000.0
SPEED_LIMIT: float = 100_000.0
DIRECTION_LIMIT: float = 1_000.0
ACTIVITY_MIN: float = 0.0
ACTIVITY_MAX: float = 100.0

# Position coordinate keys. Only x/y accepted (v1).
POSITION_KEYS: tuple[str, ...] = ("x", "y")
OBSERVATION_FIELDS: tuple[str, ...] = ("timestamp_ns", "position", "displacement", "acceleration")
REQUIRED_TOP_LEVEL: tuple[str, ...] = ("schema_version", "source", "samples")

# v2 (vision-track) field sets.
TRACK_TOP_LEVEL: tuple[str, ...] = ("schema_version", "source", "frames")
FRAME_FIELDS: tuple[str, ...] = ("timestamp", "fish_count", "activity_pct", "fish")
FISH_FIELDS: tuple[str, ...] = ("id", "centroid", "area", "speed", "direction_rad")

# Audio (ESP32 mic) field sets/bounds. One window = readings collected over
# a bounded elapsed-time capture (see server/audio_serial.py), not a
# historical log like the fish frames.
AUDIO_TOP_LEVEL: tuple[str, ...] = ("schema_version", "source", "window_duration_s", "readings")
READING_FIELDS: tuple[str, ...] = ("offset_s", "value")

MIN_READINGS: int = 1
MAX_READINGS: int = 100_000

# Sound_Level bounds: the ESP32 firmware's exact formula is opaque (out of
# scope to redesign), and the sample values quoted in the integration spec
# were ~11920-13102, so a tight bound around that range would be guessing.
# These are deliberately generous, named bounds that only reject clearly
# impossible values (negative, or absurdly large from a corrupted line).
AUDIO_VALUE_MIN: int = 0
AUDIO_VALUE_MAX: int = 1_000_000

# Per-reading offset (seconds since window start) and declared window
# duration bounds. Mirrors the v2 TIMESTAMP_SEC_* style above.
AUDIO_OFFSET_MIN: float = 0.0
AUDIO_OFFSET_MAX: float = 3_600.0
AUDIO_WINDOW_DURATION_MIN: float = 0.0
AUDIO_WINDOW_DURATION_MAX: float = 3_600.0


class SchemaError(ValueError):
    """Raised when the fish observation data violates the input contract."""


def _require_dict(data: Any, context: str) -> dict:
    if not isinstance(data, dict):
        raise SchemaError(f"{context}: expected object, got {type(data).__name__}")
    return data


def _require_number(value: Any, field: str) -> float:
    """Validate a numeric field: must be a real number, never NaN or infinity.

    Accepts int and float only - booleans are ints in Python, so reject them
    explicitly since bool is not a valid numeric observation.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"{field}: expected number, got {type(value).__name__}")
    number: float = float(value)
    if not math.isfinite(number):
        raise SchemaError(f"{field}: NaN/infinity values are not accepted")
    return number


def _require_int(value: Any, field: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{field}: expected integer, got {type(value).__name__}")
    if not lo <= value <= hi:
        raise SchemaError(f"{field}: {value} out of range [{lo}, {hi}]")
    return value


def _validate_coord(vector: Any, field: str) -> None:
    vector = _require_dict(vector, field)
    unknown = set(vector) - set(POSITION_KEYS)
    if unknown:
        raise SchemaError(f"{field}: unexpected key(s): {sorted(unknown)}")
    for axis in POSITION_KEYS:
        if axis not in vector:
            raise SchemaError(f"{field}: missing required axis '{axis}'")
        x = _require_number(vector[axis], f"{field}.{axis}")
        if not POSITION_MIN <= x <= POSITION_MAX:
            raise SchemaError(f"{field}.{axis}: value {x} out of bounded range")


def _validate_observation(sample: Any, index: int) -> None:
    sample = _require_dict(sample, f"samples[{index}]")
    unknown = set(sample) - set(OBSERVATION_FIELDS)
    if unknown:
        raise SchemaError(f"samples[{index}]: unexpected key(s): {sorted(unknown)}")

    ts = sample.get("timestamp_ns")
    if ts is None:
        raise SchemaError(f"samples[{index}]: missing required field 'timestamp_ns'")
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise SchemaError(f"samples[{index}].timestamp_ns: expected integer, got {type(ts).__name__}")
    if not TIMESTAMP_NS_MIN <= ts <= TIMESTAMP_NS_MAX:
        raise SchemaError(f"samples[{index}].timestamp_ns: {ts} out of range")

    for field in ("position", "displacement", "acceleration"):
        value = sample.get(field)
        if value is None:
            raise SchemaError(f"samples[{index}]: missing required field '{field}'")
        _validate_coord(value, f"samples[{index}].{field}")

    displacement = sample["displacement"]
    acceleration = sample["acceleration"]
    for axis in POSITION_KEYS:
        dx = _require_number(displacement[axis], f"samples[{index}].displacement.{axis}")
        if abs(dx) > DISPLACEMENT_LIMIT:
            raise SchemaError(f"samples[{index}].displacement.{axis}: |{dx}| exceeds {DISPLACEMENT_LIMIT}")
        ax = _require_number(acceleration[axis], f"samples[{index}].acceleration.{axis}")
        if abs(ax) > ACCELERATION_LIMIT:
            raise SchemaError(f"samples[{index}].acceleration.{axis}: |{ax}| exceeds {ACCELERATION_LIMIT}")


def _validate_observations_v1(data: Any) -> dict:
    """Validate the v1 sample-based contract (position/displacement/acceleration)."""
    _require_dict(data, "top level")
    unknown = set(data) - set(REQUIRED_TOP_LEVEL)
    if unknown:
        raise SchemaError(f"top level: unexpected key(s): {sorted(unknown)}")
    missing = [field for field in REQUIRED_TOP_LEVEL if field not in data]
    if missing:
        raise SchemaError(f"top level: missing required field(s): {missing}")

    if data.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError(
            f"schema_version: expected {SCHEMA_VERSION}, got {data.get('schema_version')!r}"
        )
    if data.get("source") != SOURCE_IDENTIFIER:
        raise SchemaError(f"source: expected {SOURCE_IDENTIFIER!r}, got {data.get('source')!r}")
    if not isinstance(data["samples"], list):
        raise SchemaError(f"samples: expected list, got {type(data['samples']).__name__}")

    samples = data["samples"]
    if not MIN_SAMPLES <= len(samples) <= MAX_SAMPLES:
        raise SchemaError(f"samples: count {len(samples)} outside [{MIN_SAMPLES}, {MAX_SAMPLES}]")

    for index, sample in enumerate(samples):
        _validate_observation(sample, index)

    return data


def _validate_fish(fish: Any, frame_index: int, fish_index: int) -> None:
    fish = _require_dict(fish, f"frames[{frame_index}].fish[{fish_index}]")
    unknown = set(fish) - set(FISH_FIELDS)
    if unknown:
        raise SchemaError(f"frames[{frame_index}].fish[{fish_index}]: unexpected key(s): {sorted(unknown)}")

    fid = fish.get("id")
    if fid is None:
        raise SchemaError(f"frames[{frame_index}].fish[{fish_index}]: missing required field 'id'")
    _require_int(fid, f"frames[{frame_index}].fish[{fish_index}].id", 0, FISH_ID_MAX)

    centroid = fish.get("centroid")
    if not isinstance(centroid, (list, tuple)) or len(centroid) != 2:
        raise SchemaError(
            f"frames[{frame_index}].fish[{fish_index}].centroid: expected [x, y] array"
        )
    for axis, value in zip(("x", "y"), centroid, strict=True):
        coord = _require_number(value, f"frames[{frame_index}].fish[{fish_index}].centroid.{axis}")
        if abs(coord) > CENTROID_LIMIT:
            raise SchemaError(
                f"frames[{frame_index}].fish[{fish_index}].centroid.{axis}: |{coord}| exceeds {CENTROID_LIMIT}"
            )

    area = fish.get("area")
    if area is None:
        raise SchemaError(f"frames[{frame_index}].fish[{fish_index}]: missing required field 'area'")
    a = _require_number(area, f"frames[{frame_index}].fish[{fish_index}].area")
    if not 0.0 <= a <= AREA_LIMIT:
        raise SchemaError(f"frames[{frame_index}].fish[{fish_index}].area: {a} outside [0, {AREA_LIMIT}]")

    speed = fish.get("speed")
    if speed is None:
        raise SchemaError(f"frames[{frame_index}].fish[{fish_index}]: missing required field 'speed'")
    s = _require_number(speed, f"frames[{frame_index}].fish[{fish_index}].speed")
    if not 0.0 <= s <= SPEED_LIMIT:
        raise SchemaError(f"frames[{frame_index}].fish[{fish_index}].speed: {s} outside [0, {SPEED_LIMIT}]")

    direction = fish.get("direction_rad")
    if direction is None:
        raise SchemaError(
            f"frames[{frame_index}].fish[{fish_index}]: missing required field 'direction_rad'"
        )
    d = _require_number(direction, f"frames[{frame_index}].fish[{fish_index}].direction_rad")
    if abs(d) > DIRECTION_LIMIT:
        raise SchemaError(
            f"frames[{frame_index}].fish[{fish_index}].direction_rad: |{d}| exceeds {DIRECTION_LIMIT}"
        )


def _validate_frame(frame: Any, index: int) -> None:
    frame = _require_dict(frame, f"frames[{index}]")
    unknown = set(frame) - set(FRAME_FIELDS)
    if unknown:
        raise SchemaError(f"frames[{index}]: unexpected key(s): {sorted(unknown)}")

    ts = frame.get("timestamp")
    if ts is None:
        raise SchemaError(f"frames[{index}]: missing required field 'timestamp'")
    t = _require_number(ts, f"frames[{index}].timestamp")
    if not TIMESTAMP_SEC_MIN <= t <= TIMESTAMP_SEC_MAX:
        raise SchemaError(f"frames[{index}].timestamp: {t} out of range")

    fish_count = frame.get("fish_count")
    if fish_count is None:
        raise SchemaError(f"frames[{index}]: missing required field 'fish_count'")
    _require_int(fish_count, f"frames[{index}].fish_count", 0, MAX_FISH)

    activity = frame.get("activity_pct")
    if activity is None:
        raise SchemaError(f"frames[{index}]: missing required field 'activity_pct'")
    pct = _require_number(activity, f"frames[{index}].activity_pct")
    if not ACTIVITY_MIN <= pct <= ACTIVITY_MAX:
        raise SchemaError(f"frames[{index}].activity_pct: {pct} outside [{ACTIVITY_MIN}, {ACTIVITY_MAX}]")

    fish = frame.get("fish")
    if not isinstance(fish, list):
        raise SchemaError(f"frames[{index}].fish: expected list, got {type(fish).__name__}")
    if len(fish) > MAX_FISH:
        raise SchemaError(f"frames[{index}].fish: count {len(fish)} exceeds {MAX_FISH}")
    for fish_index, item in enumerate(fish):
        _validate_fish(item, index, fish_index)

    if fish_count != len(fish):
        raise SchemaError(
            f"frames[{index}]: fish_count {fish_count} does not match "
            f"fish list length {len(fish)}"
        )


def _validate_track(data: Any) -> dict:
    """Validate the v2 vision-track contract (frames of detected fish)."""
    _require_dict(data, "top level")
    unknown = set(data) - set(TRACK_TOP_LEVEL)
    if unknown:
        raise SchemaError(f"top level: unexpected key(s): {sorted(unknown)}")
    missing = [field for field in TRACK_TOP_LEVEL if field not in data]
    if missing:
        raise SchemaError(f"top level: missing required field(s): {missing}")

    if data.get("schema_version") != TRACK_SCHEMA_VERSION:
        raise SchemaError(
            f"schema_version: expected {TRACK_SCHEMA_VERSION}, got {data.get('schema_version')!r}"
        )
    if data.get("source") != SOURCE_IDENTIFIER:
        raise SchemaError(f"source: expected {SOURCE_IDENTIFIER!r}, got {data.get('source')!r}")
    if not isinstance(data["frames"], list):
        raise SchemaError(f"frames: expected list, got {type(data['frames']).__name__}")

    frames = data["frames"]
    if not MIN_SAMPLES <= len(frames) <= MAX_FRAMES:
        raise SchemaError(f"frames: count {len(frames)} outside [{MIN_SAMPLES}, {MAX_FRAMES}]")

    for index, frame in enumerate(frames):
        _validate_frame(frame, index)

    return data


def _validate_reading(reading: Any, index: int) -> None:
    reading = _require_dict(reading, f"readings[{index}]")
    unknown = set(reading) - set(READING_FIELDS)
    if unknown:
        raise SchemaError(f"readings[{index}]: unexpected key(s): {sorted(unknown)}")

    offset = reading.get("offset_s")
    if offset is None:
        raise SchemaError(f"readings[{index}]: missing required field 'offset_s'")
    off = _require_number(offset, f"readings[{index}].offset_s")
    if not AUDIO_OFFSET_MIN <= off <= AUDIO_OFFSET_MAX:
        raise SchemaError(f"readings[{index}].offset_s: {off} out of range")

    value = reading.get("value")
    if value is None:
        raise SchemaError(f"readings[{index}]: missing required field 'value'")
    _require_int(value, f"readings[{index}].value", AUDIO_VALUE_MIN, AUDIO_VALUE_MAX)


def _validate_audio(data: Any) -> dict:
    """Validate the audio contract: a window of ESP32 Sound_Level readings."""
    _require_dict(data, "top level")
    unknown = set(data) - set(AUDIO_TOP_LEVEL)
    if unknown:
        raise SchemaError(f"top level: unexpected key(s): {sorted(unknown)}")
    missing = [field for field in AUDIO_TOP_LEVEL if field not in data]
    if missing:
        raise SchemaError(f"top level: missing required field(s): {missing}")

    if data.get("schema_version") != AUDIO_SCHEMA_VERSION:
        raise SchemaError(
            f"schema_version: expected {AUDIO_SCHEMA_VERSION}, got {data.get('schema_version')!r}"
        )
    if data.get("source") != AUDIO_SOURCE_IDENTIFIER:
        raise SchemaError(f"source: expected {AUDIO_SOURCE_IDENTIFIER!r}, got {data.get('source')!r}")

    window_duration = data.get("window_duration_s")
    wd = _require_number(window_duration, "window_duration_s")
    if not AUDIO_WINDOW_DURATION_MIN <= wd <= AUDIO_WINDOW_DURATION_MAX:
        raise SchemaError(f"window_duration_s: {wd} out of range")

    if not isinstance(data["readings"], list):
        raise SchemaError(f"readings: expected list, got {type(data['readings']).__name__}")

    readings = data["readings"]
    if not MIN_READINGS <= len(readings) <= MAX_READINGS:
        raise SchemaError(f"readings: count {len(readings)} outside [{MIN_READINGS}, {MAX_READINGS}]")

    for index, reading in enumerate(readings):
        _validate_reading(reading, index)

    return data


def validate_observations(data: Any) -> dict:
    """Validate an already-parsed object against the FISHRAND input contract.

    Dispatches on shape: `frames` → v2 vision-track, `samples` → v1 motion
    samples, `readings` → audio (ESP32 mic). Returns a deeply-validated copy
    of the data (the input object is never mutated, and mutating the result
    cannot affect any other reference to the original). Raises SchemaError
    on any violation. Never executes or evaluates anything from the payload.
    """
    _require_dict(data, "top level")
    data = copy.deepcopy(data)
    if "frames" in data:
        return _validate_track(data)
    if "samples" in data:
        return _validate_observations_v1(data)
    if "readings" in data:
        return _validate_audio(data)
    raise SchemaError(
        "top level: must contain 'samples' (v1), 'frames' (v2 vision track), or 'readings' (audio)"
    )


def load_observations(path: str | pathlib.Path) -> dict:
    """Load + validate observation JSON from a file path.

    The file is read as text and decoded with json.loads (untrusted input).
    No code is ever executed from the payload.
    """
    raw = pathlib.Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"invalid JSON: {exc}") from exc
    return validate_observations(data)


def canonicalize_observations(data: Any) -> "bytes":
    """Validate then produce the canonical byte representation.

    Delegates to the deterministic serializer in canonicalize.py so the
    interface stays exactly as specified:

        load_observations(path)
        validate_observations(data)
        canonicalize_observations(data)
    """
    from .canonicalize import canonical_bytes

    return canonical_bytes(validate_observations(data))