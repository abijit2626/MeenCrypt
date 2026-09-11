"""PART 3 - PHYSICAL OBSERVATION PROCESSING.

Computes derived metadata about the fish observation stream.

IMPORTANT SECURITY NOTE (see spec):
    position / displacement / acceleration / fish detections are NOT treated
    as independent entropy sources. They may be mathematically related
    (position -> displacement -> acceleration holds significant
    correlation; area/speed/direction come from the same detection). So this
    module treats the observations as ONE physical stream, and the derived
    statistics below are for the dashboard only. They are NEVER counted as
    independent entropy.

    The only thing that becomes a cryptographic input is the SHA-256
    digest of the ENTIRE canonical observation stream (see entropy.py).
"""

from __future__ import annotations

import statistics
import time

_VERSION = 2


def count_observations(validated: dict) -> int:
    """Number of observation units (v1 samples or v2 vision frames)."""
    if "frames" in validated:
        return len(validated["frames"])
    return len(validated.get("samples", []))


class ObservationStats:
    """Dashboard metadata derived from a validated observation stream.

    Display-only. Never used as an entropy estimate. v1 samples fill the
    `sample_*` slots; v2 vision-track frames fill the `frame_*`/`mean_*`
    slots; unused slots are None.
    """

    __slots__ = (
        # v1 sample-based slots.
        "sample_count",
        # v2 vision-track slots.
        "frame_count",
        "mean_activity_pct",
        "max_fish_count",
        "mean_speed",
        # shared slots.
        "time_span_ns",
        "mean_magnitude",
        "variance_magnitude",
        "direction_changes",
        "timing_variance_ns",
        "total_path_length",
        "window_ms",
    )

    def __init__(self) -> None:
        self.sample_count: int | None = None
        self.frame_count: int | None = None
        self.mean_activity_pct: float | None = None
        self.max_fish_count: int | None = None
        self.mean_speed: float | None = None
        self.time_span_ns: int = 0
        self.mean_magnitude: float = 0.0
        self.variance_magnitude: float = 0.0
        self.direction_changes: int = 0
        self.timing_variance_ns: int = 0
        self.total_path_length: float = 0.0
        self.window_ms: int = 0

    def to_dict(self) -> dict:
        out: dict = {"derived_by": "observe", "version": _VERSION}
        for slot in self.__slots__:
            out[slot] = getattr(self, slot)
        return out


def _vector_length(x: float, y: float) -> float:
    return (x * x + y * y) ** 0.5


def _direction_sign(a: tuple[float, float], b: tuple[float, float]) -> tuple[int, int]:
    """Return per-axis direction sign (ignoring zero movement)."""
    sx = (b[0] > a[0]) - (b[0] < a[0])
    sy = (b[1] > a[1]) - (b[1] < a[1])
    return sx, sy


def _observation_stats_v1(validated: dict) -> ObservationStats:
    """v1 sample-based metadata: {timestamp_ns, position, displacement, acceleration}."""
    samples = validated["samples"]
    count = len(samples)

    timestamps = [int(s["timestamp_ns"]) for s in samples]
    ts0, ts1 = timestamps[0], timestamps[-1]
    time_span_ns = ts1 - ts0

    magnitudes = [
        _vector_length(float(s["displacement"]["x"]), float(s["displacement"]["y"]))
        for s in samples
    ]
    mean_mag = statistics.fmean(magnitudes) if magnitudes else 0.0
    variance_mag = statistics.pvariance(magnitudes) if len(magnitudes) > 1 else 0.0

    direction_changes = 0
    prev: tuple[int, int] | None = None
    for a, b in zip(samples, samples[1:]):
        cur = _direction_sign(
            (float(a["position"]["x"]), float(a["position"]["y"])),
            (float(b["position"]["x"]), float(b["position"]["y"])),
        )
        if prev is not None and cur != prev:
            direction_changes += 1
        prev = cur

    timing_variance_ns = 0
    if len(timestamps) > 2:
        gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
        timing_variance_ns = statistics.pvariance(gaps)

    total_path_length = 0.0
    for a, b in zip(samples, samples[1:]):
        da = float(b["position"]["x"]) - float(a["position"]["x"])
        dy = float(b["position"]["y"]) - float(a["position"]["y"])
        total_path_length += _vector_length(da, dy)

    stats = ObservationStats()
    stats.sample_count = count
    stats.time_span_ns = int(time_span_ns)
    stats.mean_magnitude = float(f"{mean_mag:.4f}")
    stats.variance_magnitude = float(f"{variance_mag:.4f}")
    stats.direction_changes = int(direction_changes)
    stats.timing_variance_ns = int(timing_variance_ns)
    stats.total_path_length = float(f"{total_path_length:.4f}")
    stats.window_ms = int(time.time() * 1000)  # epoch ms of when computed
    return stats


def _observation_stats_v2(validated: dict) -> ObservationStats:
    """v2 vision-track metadata: frames of detected fish (vision.py output)."""
    frames = validated["frames"]
    count = len(frames)

    ts0, ts1 = frames[0]["timestamp"], frames[-1]["timestamp"]
    time_span_ns = int((ts1 - ts0) * 1_000_000_000)

    activities = [(f["activity_pct"]) for f in frames]
    mean_activity = statistics.fmean(activities) if activities else 0.0

    max_fish_count = max((len(f["fish"]) for f in frames), default=0)

    speeds = [fish["speed"] for frame in frames for fish in frame["fish"]]
    mean_speed = statistics.fmean(speeds) if speeds else 0.0
    variance_speed = statistics.pvariance(speeds) if len(speeds) > 1 else 0.0

    # Direction flips per tracked fish id (moving ~ahead is a direction change).
    direction_changes = 0
    total_path_length = 0.0
    for i in range(1, count):
        prev_by_id = {f["id"]: f for f in frames[i - 1]["fish"]}
        for f in frames[i]["fish"]:
            prev = prev_by_id.get(f["id"])
            if prev is None:
                continue
            if abs(f["direction_rad"] - prev["direction_rad"]) >= 0.1:
                direction_changes += 1
            px, py = prev["centroid"]
            cx, cy = f["centroid"]
            total_path_length += _vector_length(cx - px, cy - py)

    stats = ObservationStats()
    stats.frame_count = count
    stats.time_span_ns = int(time_span_ns)
    stats.mean_activity_pct = float(f"{mean_activity:.4f}")
    stats.max_fish_count = int(max_fish_count)
    stats.mean_speed = float(f"{mean_speed:.4f}")
    stats.variance_magnitude = float(f"{variance_speed:.4f}")
    stats.direction_changes = int(direction_changes)
    stats.total_path_length = float(f"{total_path_length:.4f}")
    stats.window_ms = int(time.time() * 1000)
    return stats


def observation_stats(validated: dict) -> ObservationStats:
    """Compute derived, display-only metadata from VALIDATED observations.

    Assumes the data already passed validate_observations().
    """
    if "frames" in validated:
        return _observation_stats_v2(validated)
    return _observation_stats_v1(validated)