"""Fish observation quality classification (GOOD / MEDIUM / BAD).

Chooses whether an encryption session should use fish-only, fish+audio, or
audio-only observations (see fishrand/api.py encrypt_with_observation).

Not derivable with confidence from any existing code: the only precedents
in the repo are vision.py's `speed > 0.5` (a VISUALIZATION-only threshold
that just decides whether to draw a movement arrow, not a quality measure)
and `MIN_CONTOUR_AREA = 150` (a detection-noise floor, not a quality
measure). This module defines a NEW, explicit, fully configurable
threshold scheme built on the real fields the v2 camera format reports
(fish_count, activity_pct), with a documented v1-legacy fallback. Callers
should treat the bundled defaults as a starting point to tune empirically
against a real camera, not as calibrated constants.
"""

from __future__ import annotations

from typing import Literal

from .observe import ObservationStats, observation_stats

Quality = Literal["GOOD", "MEDIUM", "BAD"]

# Defaults - not derived from any existing threshold, chosen as reasonable
# midpoints on the real 0-100 activity_pct scale (schema.py ACTIVITY_MIN/MAX)
# and the camera's MAX_FISH=10 cap (vision.py). Tune against real footage.
DEFAULT_GOOD_FISH_COUNT_MIN = 2
DEFAULT_GOOD_ACTIVITY_PCT_MIN = 5.0
DEFAULT_MEDIUM_FISH_COUNT_MIN = 1
DEFAULT_MEDIUM_ACTIVITY_PCT_MIN = 1.0


def classify_fish_quality(
    validated_fish: dict,
    stats: ObservationStats | None = None,
    *,
    good_fish_count_min: int = DEFAULT_GOOD_FISH_COUNT_MIN,
    good_activity_pct_min: float = DEFAULT_GOOD_ACTIVITY_PCT_MIN,
    medium_fish_count_min: int = DEFAULT_MEDIUM_FISH_COUNT_MIN,
    medium_activity_pct_min: float = DEFAULT_MEDIUM_ACTIVITY_PCT_MIN,
) -> Quality:
    """Classify a validated fish observation window's quality.

    validated_fish MUST have already passed schema.validate_observations().

    v2 (vision-track, the real camera format) uses observation_stats()'s
    max_fish_count / mean_activity_pct against the given thresholds:
        GOOD   : max_fish_count >= good_fish_count_min
                 AND mean_activity_pct >= good_activity_pct_min
        MEDIUM : max_fish_count >= medium_fish_count_min
                 AND mean_activity_pct >= medium_activity_pct_min
        BAD    : otherwise (includes fish_count == 0 / activity == 0.0,
                 which the schema already accepts as structurally valid
                 but which represents no usable signal)

    v1 (legacy samples) has no fish_count/activity_pct field at all, so
    this falls back to observe.py's mean_magnitude/total_path_length as a
    defensible proxy for "how much physical movement happened" - it is a
    fallback for the legacy format, not a precise equivalent of the v2
    thresholds, and reuses the same threshold values for lack of a better
    established scale.
    """
    if stats is None:
        stats = observation_stats(validated_fish)

    if "frames" in validated_fish:
        fish_count = stats.max_fish_count or 0
        activity = stats.mean_activity_pct or 0.0
        if fish_count >= good_fish_count_min and activity >= good_activity_pct_min:
            return "GOOD"
        if fish_count >= medium_fish_count_min and activity >= medium_activity_pct_min:
            return "MEDIUM"
        return "BAD"

    # v1 legacy fallback: no fish_count/activity_pct fields exist here.
    magnitude = stats.mean_magnitude
    path_length = stats.total_path_length
    if magnitude >= good_activity_pct_min and path_length >= good_fish_count_min:
        return "GOOD"
    if magnitude >= medium_activity_pct_min and path_length >= medium_fish_count_min:
        return "MEDIUM"
    return "BAD"


__all__ = ["Quality", "classify_fish_quality"]
