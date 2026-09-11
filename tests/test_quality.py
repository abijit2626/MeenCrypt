"""Tests for the fish-quality classifier (fishrand/quality.py)."""

from __future__ import annotations

import json

from fishrand.observe import ObservationStats
from fishrand.quality import classify_fish_quality
from fishrand.schema import validate_observations

DEFAULTS = dict(
    good_fish_count_min=2,
    good_activity_pct_min=5.0,
    medium_fish_count_min=1,
    medium_activity_pct_min=1.0,
)


def _track(fish_count: int, activity_pct: float):
    fish = [
        {"id": i, "centroid": [float(i), float(i)], "area": 100.0, "speed": 1.0, "direction_rad": 0.0}
        for i in range(fish_count)
    ]
    return {
        "schema_version": 2,
        "source": "fish_vision",
        "frames": [
            {"timestamp": 1750000000.0, "fish_count": fish_count, "activity_pct": activity_pct, "fish": fish},
        ],
    }


def test_good_at_exact_thresholds():
    data = validate_observations(_track(fish_count=2, activity_pct=5.0))
    assert classify_fish_quality(data, **DEFAULTS) == "GOOD"


def test_good_above_thresholds():
    data = validate_observations(_track(fish_count=5, activity_pct=50.0))
    assert classify_fish_quality(data, **DEFAULTS) == "GOOD"


def test_medium_at_exact_thresholds():
    data = validate_observations(_track(fish_count=1, activity_pct=1.0))
    assert classify_fish_quality(data, **DEFAULTS) == "MEDIUM"


def test_medium_enough_fish_but_not_enough_activity_for_good():
    # 2 fish (meets GOOD's fish-count bar) but activity below GOOD's floor.
    data = validate_observations(_track(fish_count=2, activity_pct=4.9))
    assert classify_fish_quality(data, **DEFAULTS) == "MEDIUM"


def test_bad_no_fish_no_activity():
    data = validate_observations(_track(fish_count=0, activity_pct=0.0))
    assert classify_fish_quality(data, **DEFAULTS) == "BAD"


def test_bad_below_medium_activity_floor():
    data = validate_observations(_track(fish_count=1, activity_pct=0.5))
    assert classify_fish_quality(data, **DEFAULTS) == "BAD"


def test_bad_is_schema_valid_zero_fish_case():
    # fish_count=0/activity_pct=0.0 is a structurally VALID v2 frame (see
    # schema.py) - classify_fish_quality must still call it BAD, not error.
    data = validate_observations(_track(fish_count=0, activity_pct=0.0))
    assert classify_fish_quality(data) == "BAD"


def test_defaults_used_when_no_thresholds_given():
    data = validate_observations(_track(fish_count=2, activity_pct=10.0))
    assert classify_fish_quality(data) == "GOOD"


def test_custom_thresholds_override_defaults():
    data = validate_observations(_track(fish_count=1, activity_pct=1.0))
    # With a stricter custom GOOD bar this is still just MEDIUM...
    assert classify_fish_quality(
        data, good_fish_count_min=1, good_activity_pct_min=1.0,
        medium_fish_count_min=1, medium_activity_pct_min=0.5,
    ) == "GOOD"
    # ...but relaxing the GOOD bar to match makes it GOOD.
    assert classify_fish_quality(
        data, good_fish_count_min=10, good_activity_pct_min=50.0,
        medium_fish_count_min=1, medium_activity_pct_min=0.5,
    ) == "MEDIUM"


def test_multi_frame_window_uses_max_fish_and_mean_activity():
    data = {
        "schema_version": 2,
        "source": "fish_vision",
        "frames": [
            {"timestamp": 1.0, "fish_count": 0, "activity_pct": 0.0, "fish": []},
            {"timestamp": 2.0, "fish_count": 3, "activity_pct": 10.0, "fish": [
                {"id": 0, "centroid": [0.0, 0.0], "area": 1.0, "speed": 1.0, "direction_rad": 0.0},
                {"id": 1, "centroid": [1.0, 1.0], "area": 1.0, "speed": 1.0, "direction_rad": 0.0},
                {"id": 2, "centroid": [2.0, 2.0], "area": 1.0, "speed": 1.0, "direction_rad": 0.0},
            ]},
        ],
    }
    validated = validate_observations(data)
    # max_fish_count=3 (from frame 2), mean_activity_pct=(0.0+10.0)/2=5.0
    assert classify_fish_quality(validated, **DEFAULTS) == "GOOD"


# --- v1 legacy fallback ------------------------------------------------

V1_MINIMAL = {
    "schema_version": 1,
    "source": "fish_vision",
    "samples": [
        {"timestamp_ns": 0, "position": {"x": 0.0, "y": 0.0},
         "displacement": {"x": 0.0, "y": 0.0}, "acceleration": {"x": 0.0, "y": 0.0}},
    ],
}


def _v1_stats(mean_magnitude: float, total_path_length: float) -> ObservationStats:
    stats = ObservationStats()
    stats.sample_count = 1
    stats.mean_magnitude = mean_magnitude
    stats.total_path_length = total_path_length
    return stats


def test_v1_fallback_good():
    data = json.loads(json.dumps(V1_MINIMAL))
    stats = _v1_stats(mean_magnitude=50.0, total_path_length=100.0)
    assert classify_fish_quality(data, stats, **DEFAULTS) == "GOOD"


def test_v1_fallback_medium():
    data = json.loads(json.dumps(V1_MINIMAL))
    stats = _v1_stats(mean_magnitude=1.0, total_path_length=1.0)
    assert classify_fish_quality(data, stats, **DEFAULTS) == "MEDIUM"


def test_v1_fallback_bad():
    data = json.loads(json.dumps(V1_MINIMAL))
    stats = _v1_stats(mean_magnitude=0.0, total_path_length=0.0)
    assert classify_fish_quality(data, stats, **DEFAULTS) == "BAD"
