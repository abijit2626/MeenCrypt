import json

import pytest

from fishrand.canonicalize import canonical_bytes, canonical_number
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
        },
        {
            "timestamp_ns": 500100000,
            "position": {"x": 125.0, "y": 238.0},
            "displacement": {"x": 5.1, "y": -2.0},
            "acceleration": {"x": 1.9, "y": -0.4},
        },
    ],
}


def _data_with_permuted_keys():
    """Same physical dataset, keys in different order and JSON formatting."""
    data = json.loads(json.dumps(VALID))
    reordered = {
        "source": data["source"],
        "schema_version": data["schema_version"],
        "samples": [
            {
                "acceleration": s["acceleration"],
                "position": {"y": s["position"]["y"], "x": s["position"]["x"]},
                "timestamp_ns": s["timestamp_ns"],
                "displacement": {"y": s["displacement"]["y"], "x": s["displacement"]["x"]},
            }
            for s in data["samples"]
        ],
    }
    return reordered


def test_same_data_same_bytes():
    a = canonical_bytes(validate_observations(VALID))
    b = canonical_bytes(validate_observations(_data_with_permuted_keys()))
    assert a == b


def test_different_data_different_bytes():
    altered = json.loads(json.dumps(VALID))
    altered["samples"][0]["position"]["x"] += 1.0
    assert canonical_bytes(validate_observations(VALID)) != canonical_bytes(validate_observations(altered))


def test_canonical_output_is_utf8_no_whitespace():
    blob = canonical_bytes(validate_observations(VALID))
    assert isinstance(blob, bytes)
    # No newline or whitespace padding anywhere in the canonical stream.
    assert b"\n" not in blob
    assert blob.decode("utf-8")  # valid UTF-8
    # explicit version + source embedded
    assert b'"schema_version",1,"source","fish_vision","samples"' in blob


def test_fixed_field_ordering_per_sample():
    blob = canonical_bytes(validate_observations(VALID)).decode("utf-8")
    assert blob.index('"timestamp_ns"') < blob.index('"position"')
    assert blob.index('"position"') < blob.index('"displacement"')
    assert blob.index('"displacement"') < blob.index('"acceleration"')
    assert blob.count('"x"') >= 2


@pytest.mark.parametrize(
    "value,expected",
    [
        (1, "1"),
        (1.0, "1.0"),
        (120.5, "120.5"),
        (3.14, "3.14"),
        (-1.4, "-1.4"),
        (0, "0"),
    ],
)
def test_canonical_number_deterministic(value, expected):
    assert canonical_number(value) == expected


def test_canonical_number_rejects_nan_inf():
    with pytest.raises(ValueError):
        canonical_number(float("nan"))
    with pytest.raises(ValueError):
        canonical_number(float("inf"))


def test_int_and_matching_float_are_distinct_but_faithful():
    # x=1 (int) vs x=1 (float 1.0) are equal as *values*; canonical form
    # keeps type signature explicit so the mapping never flips silently.
    from fishrand.canonicalize import canonical_number

    assert canonical_number(1) == "1"
    assert canonical_number(1.0) == "1.0"


TRACK = {
    "schema_version": 2,
    "source": "fish_vision",
    "frames": [
        {"timestamp": 1750000000.0, "fish_count": 1, "activity_pct": 4.2,
         "fish": [{"id": 0, "centroid": [120.5, 240.2], "area": 320.5,
                   "speed": 3.2, "direction_rad": 0.2}]},
        {"timestamp": 1750000005.0, "fish_count": 0, "activity_pct": 0.0, "fish": []},
    ],
}


def _track_with_permuted_keys():
    data = json.loads(json.dumps(TRACK))
    return {
        "source": data["source"],
        "schema_version": data["schema_version"],
        "frames": [
            {
                "activity_pct": f["activity_pct"],
                "fish_count": f["fish_count"],
                "fish": [
                    {"speed": fish["speed"], "direction_rad": fish["direction_rad"],
                     "centroid": [fish["centroid"][0], fish["centroid"][1]],
                     "area": fish["area"], "id": fish["id"]}
                    for fish in f["fish"]
                ],
                "timestamp": f["timestamp"],
            }
            for f in data["frames"]
        ],
    }


def test_track_same_data_same_bytes():
    assert canonical_bytes(validate_observations(TRACK)) == canonical_bytes(
        validate_observations(_track_with_permuted_keys())
    )


def test_track_different_data_different_bytes():
    altered = json.loads(json.dumps(TRACK))
    altered["frames"][0]["fish"][0]["area"] -= 1.0
    assert canonical_bytes(validate_observations(TRACK)) != canonical_bytes(
        validate_observations(altered)
    )


def test_track_canonical_embeds_version_and_frames():
    blob = canonical_bytes(validate_observations(TRACK))
    assert b'"schema_version",2,"source","fish_vision","frames"' in blob
    assert b"frame" in blob


def test_track_fixed_field_ordering_per_frame():
    blob = canonical_bytes(validate_observations(TRACK)).decode("utf-8")
    assert blob.index('"timestamp"') < blob.index('"fish_count"')
    assert blob.index('"fish_count"') < blob.index('"activity_pct"')
    assert blob.index('"activity_pct"') < blob.index('"fish"')


def test_track_fish_field_ordering():
    blob = canonical_bytes(validate_observations(TRACK)).decode("utf-8")
    assert blob.index('"id"') < blob.index('"centroid"')
    assert blob.index('"centroid"') < blob.index('"area"')
    assert blob.index('"area"') < blob.index('"speed"')
    assert blob.index('"speed"') < blob.index('"direction_rad"')
    assert blob.index('"direction_rad"') > 0


def test_empty_fish_list_canonicalization():
    empty = {"schema_version": 2, "source": "fish_vision", "frames": [
        {"timestamp": 1.0, "fish_count": 0, "activity_pct": 0.0, "fish": []},
    ]}
    assert b'"fish",[]' in canonical_bytes(validate_observations(empty))


# --- Audio (ESP32 mic "readings") ------------------------------------------

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


def _audio_with_permuted_keys():
    data = json.loads(json.dumps(AUDIO))
    return {
        "source": data["source"],
        "schema_version": data["schema_version"],
        "readings": [
            {"value": r["value"], "offset_s": r["offset_s"]} for r in data["readings"]
        ],
        "window_duration_s": data["window_duration_s"],
    }


def test_audio_same_data_same_bytes():
    a = canonical_bytes(validate_observations(AUDIO))
    b = canonical_bytes(validate_observations(_audio_with_permuted_keys()))
    assert a == b


def test_audio_different_data_different_bytes():
    altered = json.loads(json.dumps(AUDIO))
    altered["readings"][0]["value"] += 1
    assert canonical_bytes(validate_observations(AUDIO)) != canonical_bytes(
        validate_observations(altered)
    )


def test_audio_changed_offset_changes_bytes():
    altered = json.loads(json.dumps(AUDIO))
    altered["readings"][0]["offset_s"] += 0.001
    assert canonical_bytes(validate_observations(AUDIO)) != canonical_bytes(
        validate_observations(altered)
    )


def test_audio_changed_window_duration_changes_bytes():
    altered = json.loads(json.dumps(AUDIO))
    altered["window_duration_s"] += 1.0
    assert canonical_bytes(validate_observations(AUDIO)) != canonical_bytes(
        validate_observations(altered)
    )


def test_audio_canonical_embeds_version_and_source():
    blob = canonical_bytes(validate_observations(AUDIO))
    assert b'"schema_version",1,"source","esp32_mic","window_duration_s"' in blob


def test_audio_fixed_field_ordering_per_reading():
    blob = canonical_bytes(validate_observations(AUDIO)).decode("utf-8")
    assert blob.index('"offset_s"') < blob.index('"value"')


def test_audio_bytes_disjoint_from_fish_bytes():
    # Same physical numbers, different shape -> must not collide.
    fish_like = {"schema_version": 1, "source": "fish_vision", "samples": [
        {"timestamp_ns": 0, "position": {"x": 0.0, "y": 0.0},
         "displacement": {"x": 0.0, "y": 0.0}, "acceleration": {"x": 0.0, "y": 0.0}},
    ]}
    assert canonical_bytes(validate_observations(AUDIO)) != canonical_bytes(
        validate_observations(fish_like)
    )