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


# --- Audio digest + fish/audio combination ----------------------------------

from fishrand.entropy import audio_digest_bytes, audio_digest_hex, combined_observation_digest  # noqa: E402
from fishrand.schema import AUDIO_SCHEMA_VERSION, AUDIO_SOURCE_IDENTIFIER  # noqa: E402

AUDIO = {
    "schema_version": AUDIO_SCHEMA_VERSION,
    "source": AUDIO_SOURCE_IDENTIFIER,
    "window_duration_s": 5.0,
    "readings": [
        {"offset_s": 0.0, "value": 12345},
        {"offset_s": 1.2, "value": 12890},
    ],
}


def test_audio_digest_is_32_bytes():
    digest = audio_digest_bytes(validate_observations(AUDIO))
    assert isinstance(digest, bytes)
    assert len(digest) == 32


def test_audio_digest_hex_matches_digest():
    assert audio_digest_hex(validate_observations(AUDIO)) == audio_digest_bytes(
        validate_observations(AUDIO)
    ).hex()


def test_audio_digest_is_deterministic():
    assert audio_digest_bytes(validate_observations(AUDIO)) == audio_digest_bytes(
        validate_observations(json.loads(json.dumps(AUDIO)))
    )


def test_audio_digest_changes_on_any_reading_change():
    altered = json.loads(json.dumps(AUDIO))
    altered["readings"][0]["value"] += 1
    assert audio_digest_bytes(validate_observations(AUDIO)) != audio_digest_bytes(
        validate_observations(altered)
    )


def test_combined_digest_is_32_bytes():
    fish_d = fish_digest_bytes(validate_observations(TRACK))
    audio_d = audio_digest_bytes(validate_observations(AUDIO))
    combined = combined_observation_digest(fish_d, audio_d)
    assert isinstance(combined, bytes)
    assert len(combined) == 32


def test_combined_digest_deterministic():
    fish_d = fish_digest_bytes(validate_observations(TRACK))
    audio_d = audio_digest_bytes(validate_observations(AUDIO))
    assert combined_observation_digest(fish_d, audio_d) == combined_observation_digest(fish_d, audio_d)


def test_combined_digest_changes_with_fish():
    fish_d = fish_digest_bytes(validate_observations(TRACK))
    audio_d = audio_digest_bytes(validate_observations(AUDIO))
    altered_fish = json.loads(json.dumps(TRACK))
    altered_fish["frames"][0]["fish"][0]["area"] += 1.0
    altered_fish_d = fish_digest_bytes(validate_observations(altered_fish))
    assert combined_observation_digest(fish_d, audio_d) != combined_observation_digest(altered_fish_d, audio_d)


def test_combined_digest_changes_with_audio():
    fish_d = fish_digest_bytes(validate_observations(TRACK))
    audio_d = audio_digest_bytes(validate_observations(AUDIO))
    altered_audio = json.loads(json.dumps(AUDIO))
    altered_audio["readings"][0]["value"] += 1
    altered_audio_d = audio_digest_bytes(validate_observations(altered_audio))
    assert combined_observation_digest(fish_d, audio_d) != combined_observation_digest(fish_d, altered_audio_d)


def test_combined_digest_is_domain_separated_not_a_passthrough():
    # The combination must not just be one of the raw digests re-emitted.
    fish_d = fish_digest_bytes(validate_observations(TRACK))
    audio_d = audio_digest_bytes(validate_observations(AUDIO))
    combined = combined_observation_digest(fish_d, audio_d)
    assert combined != fish_d
    assert combined != audio_d


def test_combined_digest_rejects_wrong_length_input():
    with pytest.raises(ValueError):
        combined_observation_digest(b"short", b"x" * 32)
    with pytest.raises(ValueError):
        combined_observation_digest(b"x" * 32, b"short")