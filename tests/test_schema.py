import json

import pytest

from fishrand import SchemaError, canonicalize_observations, load_observations, validate_observations
from fishrand.schema import SCHEMA_VERSION, SOURCE_IDENTIFIER

VALID = {
    "schema_version": SCHEMA_VERSION,
    "source": SOURCE_IDENTIFIER,
    "samples": [
        {
            "timestamp_ns": 500000000,
            "position": {"x": 120.5, "y": 240.2},
            "displacement": {"x": 3.2, "y": -1.4},
            "acceleration": {"x": 0.8, "y": 0.2},
        },
        {
            "timestamp_ns": 500100000,
            "position": {"x": 125.0, "y": 238.0},
            "displacement": {"x": 5.1, "y": -2.0},
            "acceleration": {"x": 1.9, "y": -0.4},
        },
    ],
}


@pytest.fixture
def valid():
    return json.loads(json.dumps(VALID))


def test_valid_passes(valid):
    assert validate_observations(valid) == valid


def test_missing_top_level_field(valid):
    del valid["samples"]
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_wrong_schema_version(valid):
    valid["schema_version"] = 99
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_wrong_source(valid):
    valid["source"] = "evil_camera"
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_samples_not_list(valid):
    valid["samples"] = "not-a-list"
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_empty_samples_rejected(valid):
    valid["samples"] = []
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_too_many_samples_rejected():
    from fishrand.schema import MAX_SAMPLES

    data = json.loads(json.dumps(VALID))
    data["samples"] = [data["samples"][0]] * (MAX_SAMPLES + 1)
    with pytest.raises(SchemaError):
        validate_observations(data)


def test_missing_field_in_sample(valid):
    del valid["samples"][0]["position"]
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_unexpected_key_in_sample(valid):
    valid["samples"][0]["__dict__"] = 1
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_unexpected_top_level_key(valid):
    valid["eval_payload"] = "danger"
    with pytest.raises(SchemaError):
        validate_observations(valid)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinity_rejected(valid, value):
    valid["samples"][0]["position"]["x"] = value
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_string_instead_of_number(valid):
    valid["samples"][0]["displacement"]["x"] = "twelve"
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_bool_rejected_as_number(valid):
    valid["samples"][0]["acceleration"]["y"] = True
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_timestamp_string_rejected(valid):
    valid["samples"][0]["timestamp_ns"] = "500"
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_timestamp_out_of_range(valid):
    valid["samples"][0]["timestamp_ns"] = -5
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_position_out_of_bounds(valid):
    valid["samples"][0]["position"]["y"] = 1_000_000
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_extremely_large_position_rejected(valid):
    import math

    valid["samples"][0]["position"]["x"] = math.ldexp(1.0, 1000)
    with pytest.raises(SchemaError):
        validate_observations(valid)


def test_load_observations_from_json(tmp_path):
    path = tmp_path / "obs.json"
    path.write_text(json.dumps(VALID))
    assert load_observations(path) == VALID


def test_load_invalid_json(tmp_path):
    path = tmp_path / "obs.json"
    path.write_text("{not json")
    with pytest.raises(SchemaError):
        load_observations(path)


def test_canonicalize_observations_is_deterministic_across_formatting(valid):
    a = canonicalize_observations(json.loads(json.dumps(valid, indent=2)))
    b = canonicalize_observations(json.loads(json.dumps(valid, sort_keys=True, separators=(",", ":"))))
    c = canonicalize_observations(valid)
    assert a == b == c


def test_numpy_free_no_code_execution(valid):
    # Payload with a dangerous-looking key must be rejected, never evaluated.
    valid["samples"][0]["__import__('os').system('true')"] = 1
    with pytest.raises(SchemaError):
        canonicalize_observations(valid)


TRACK = {
    "schema_version": 2,
    "source": SOURCE_IDENTIFIER,
    "frames": [
        {
            "timestamp": 1750000000.0,
            "fish_count": 1,
            "activity_pct": 4.2,
            "fish": [{"id": 0, "centroid": [120.5, 240.2], "area": 320.5,
                      "speed": 3.2, "direction_rad": 0.2}],
        },
        {
            "timestamp": 1750000005.0,
            "fish_count": 0,
            "activity_pct": 0.0,
            "fish": [],
        },
    ],
}


@pytest.fixture
def track():
    return json.loads(json.dumps(TRACK))


def test_v2_track_passes(track):
    assert validate_observations(track) == track


def test_v2_version_number_and_frames(track):
    from fishrand.schema import TRACK_SCHEMA_VERSION
    from fishrand.canonicalize import canonical_bytes

    assert TRACK_SCHEMA_VERSION == 2
    assert b'"schema_version",2' in canonical_bytes(validate_observations(track))
    assert b'"frames"' in canonical_bytes(validate_observations(track))


def test_v2_missing_frames_rejected(track):
    del track["frames"]
    with pytest.raises(SchemaError):
        validate_observations(track)


def test_v2_wrong_version(track):
    track["schema_version"] = 3
    with pytest.raises(SchemaError):
        validate_observations(track)


def test_v2_empty_frames_rejected(track):
    track["frames"] = []
    with pytest.raises(SchemaError):
        validate_observations(track)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t["frames"][0].update({"timestamp": "oops"}),
        lambda t: t["frames"][0].pop("fish_count"),
        lambda t: t["frames"][0].update({"activity_pct": -1}),
        lambda t: t["frames"][0].update({"fish_count": -1}),
        lambda t: t["frames"][0]["fish"][0].pop("centroid"),
        lambda t: t["frames"][0]["fish"][0].update({"id": -3}),
        lambda t: t["frames"][0]["fish"][0].update({"speed": float("nan")}),
    ],
)
def test_v2_malformed_frame_rejected(track, mutate):
    mutate(track)
    with pytest.raises(SchemaError):
        validate_observations(track)


def test_v2_extra_frame_key_rejected(track):
    track["frames"][0]["malware"] = "x"
    with pytest.raises(SchemaError):
        validate_observations(track)


def test_v2_too_many_frames_rejected(track):
    from fishrand.schema import MAX_FRAMES

    track["frames"] = [track["frames"][0]] * (MAX_FRAMES + 1)
    with pytest.raises(SchemaError):
        validate_observations(track)


def test_v2_canonicalize_deterministic_across_formatting(track):
    import json as _json
    from fishrand import canonicalize_observations

    a = canonicalize_observations(_json.loads(_json.dumps(track, indent=2)))
    b = canonicalize_observations(_json.loads(_json.dumps(track, sort_keys=True, separators=(",", ":"))))
    c = canonicalize_observations(track)
    assert a == b == c


# --- Audio (ESP32 mic "readings") ------------------------------------------

from fishrand.schema import AUDIO_SCHEMA_VERSION, AUDIO_SOURCE_IDENTIFIER  # noqa: E402

AUDIO = {
    "schema_version": AUDIO_SCHEMA_VERSION,
    "source": AUDIO_SOURCE_IDENTIFIER,
    "window_duration_s": 5.0,
    "readings": [
        {"offset_s": 0.0, "value": 12345},
        {"offset_s": 1.2, "value": 12890},
        {"offset_s": 2.4, "value": 11920},
    ],
}


@pytest.fixture
def audio():
    return json.loads(json.dumps(AUDIO))


def test_audio_valid_passes(audio):
    assert validate_observations(audio) == audio


def test_audio_wrong_schema_version(audio):
    audio["schema_version"] = 99
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_wrong_source(audio):
    audio["source"] = "some_other_mic"
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_missing_readings(audio):
    del audio["readings"]
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_empty_readings_rejected(audio):
    audio["readings"] = []
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_too_many_readings_rejected():
    from fishrand.schema import MAX_READINGS

    data = json.loads(json.dumps(AUDIO))
    data["readings"] = [data["readings"][0]] * (MAX_READINGS + 1)
    with pytest.raises(SchemaError):
        validate_observations(data)


def test_audio_negative_value_rejected(audio):
    audio["readings"][0]["value"] = -1
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_non_numeric_value_rejected(audio):
    audio["readings"][0]["value"] = "twelve"
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_value_out_of_bounds_rejected(audio):
    from fishrand.schema import AUDIO_VALUE_MAX

    audio["readings"][0]["value"] = AUDIO_VALUE_MAX + 1
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_missing_offset_rejected(audio):
    del audio["readings"][0]["offset_s"]
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_unexpected_reading_key_rejected(audio):
    audio["readings"][0]["extra"] = 1
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_unexpected_top_level_key_rejected(audio):
    audio["eval_payload"] = "danger"
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_missing_window_duration_rejected(audio):
    del audio["window_duration_s"]
    with pytest.raises(SchemaError):
        validate_observations(audio)


def test_audio_dispatch_does_not_collide_with_fish_shapes(audio):
    # A "readings" top-level key must never be mistaken for v1 "samples"
    # or v2 "frames" - and vice versa.
    assert "samples" not in audio and "frames" not in audio
    with pytest.raises(SchemaError):
        validate_observations({"schema_version": 1, "source": "fish_vision"})