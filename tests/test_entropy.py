import json

import pytest

from fishrand.entropy import condition_observations, fish_digest_bytes, fish_digest_hex
from fishrand.schema import validate_observations

VALID = {
    "schema_version": 1,
    "source": "fish_vision",
    "samples": [
        {
            "timestamp_ns": 500000000,
            "position": {"x": 120.5, "y": 240.2},
            "displacement": {"x": 3.2, "y": -1.4},
            "acceleration": {"x": 0.8, "y": 0.2},
        }
    ],
}


def test_digest_is_32_bytes():
    digest = fish_digest_bytes(validate_observations(VALID))
    assert isinstance(digest, bytes)
    assert len(digest) == 32


def test_digest_hex_matches_digest():
    assert fish_digest_hex(validate_observations(VALID)) == fish_digest_bytes(
        validate_observations(VALID)
    ).hex()


def test_digest_is_deterministic():
    assert fish_digest_bytes(validate_observations(VALID)) == fish_digest_bytes(
        validate_observations(json.loads(json.dumps(VALID)))
    )


def test_digest_changes_on_any_observation_change():
    altered = json.loads(json.dumps(VALID))
    altered["samples"][0]["acceleration"]["y"] += 0.13
    assert fish_digest_bytes(validate_observations(VALID)) != fish_digest_bytes(
        validate_observations(altered)
    )


def test_condition_observations_validates_input():
    bad = json.loads(json.dumps(VALID))
    bad["samples"] = []
    with pytest.raises(Exception):
        condition_observations(bad)


TRACK = {
    "schema_version": 2,
    "source": "fish_vision",
    "frames": [
        {"timestamp": 1750000000.0, "fish_count": 1, "activity_pct": 4.2,
         "fish": [{"id": 0, "centroid": [120.5, 240.2], "area": 320.5,
                   "speed": 3.2, "direction_rad": 0.2}]},
        {"timestamp": 1750000005.0, "fish_count": 1, "activity_pct": 2.0,
         "fish": [{"id": 0, "centroid": [125.0, 238.0], "area": 331.0,
                   "speed": 5.1, "direction_rad": -0.4}]},
    ],
}


def test_track_digest_is_32_bytes():
    digest = fish_digest_bytes(validate_observations(TRACK))
    assert len(digest) == 32


def test_track_digest_changes_on_any_observation_change():
    altered = json.loads(json.dumps(TRACK))
    altered["frames"][0]["fish"][0]["area"] += 1.0
    assert fish_digest_bytes(validate_observations(TRACK)) != fish_digest_bytes(
        validate_observations(altered)
    )