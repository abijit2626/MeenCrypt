import json

import pytest

from fishrand.schema import SchemaError
from server.collector import (
    FishSourceError,
    _normalize_raw,
    _window_from_log,
    collect_fish,
    current_source,
)


def test_fallback_used_when_no_live_or_inbox():
    data = collect_fish()
    assert data["source"] == "fish_vision"
    assert data["schema_version"] == 2
    assert isinstance(data["frames"], list)
    assert len(data["frames"]) >= 1


def test_fallback_is_contract_valid():
    data = collect_fish()
    from fishrand.schema import validate_observations

    assert validate_observations(data) == data


def test_current_source_labels_fallback():
    assert current_source().startswith("fallback:")


def test_raw_minimal_format_normalized():
    raw = {
        "samples": [
            {"position": {"x": 1, "y": 2}, "displacement": {"x": 0.1, "y": 0.0},
             "acceleration": {"x": 0.0, "y": 0.1}},
            {"position": {"x": 2, "y": 2}, "displacement": {"x": 1.0, "y": 0.0},
             "acceleration": {"x": 0.9, "y": 0.0}},
        ]
    }
    normalized = _normalize_raw(raw)
    assert normalized["schema_version"] == 1
    assert normalized["source"] == "fish_vision"
    assert "timestamp_ns" in normalized["samples"][0]
    assert isinstance(normalized["samples"][0]["timestamp_ns"], int)


def test_raw_vision_frame_normalized_to_v2():
    frame = {
        "timestamp": 1750000000.0,
        "fish_count": 1,
        "activity_pct": 4.2,
        "fish": [{"id": 0, "centroid": [120.5, 240.2], "area": 320.5, "speed": 3.2, "direction_rad": 0.2}],
    }
    normalized = _normalize_raw(frame)
    assert normalized["schema_version"] == 2
    assert len(normalized["frames"]) == 1


def test_raw_frames_list_normalized_to_v2():
    frame = {
        "timestamp": 1750000000.0, "fish_count": 0, "activity_pct": 0.0, "fish": [],
    }
    normalized = _normalize_raw([frame, frame])
    assert normalized["schema_version"] == 2
    assert len(normalized["frames"]) == 2


def test_full_v2_contract_passes_through():
    raw = {"schema_version": 2, "source": "fish_vision", "frames": [
        {"timestamp": 1.0, "fish_count": 0, "activity_pct": 0.0, "fish": []},
    ]}
    assert _normalize_raw(raw) is raw


_FRAME = {
    "timestamp": 1750000000.0, "fish_count": 1, "activity_pct": 4.0,
    "fish": [{"id": 0, "centroid": [10.0, 20.0], "area": 100.0, "speed": 1.0, "direction_rad": 0.0}],
}


def _ndjson(frames, start=0.0, step=5.0):
    docs = []
    for i, frame in enumerate(frames):
        f = json.loads(json.dumps(frame))
        f["timestamp"] = start + i * step
        docs.append(json.dumps(f))
    return "\n".join(docs) + "\n"


def test_ndjson_inbox_bundles_newest_frames():
    log = _ndjson([_FRAME] * 10, start=100.0)
    envelope, kind = _window_from_log(log, max_frames=4)
    assert kind == "v2"
    assert len(envelope["frames"]) == 4
    assert envelope["frames"][-1]["timestamp"] == 145.0  # newest kept


def test_single_v1_window_passes_through():
    raw = {"schema_version": 1, "source": "fish_vision", "samples": [
        {"timestamp_ns": 1, "position": {"x": 0, "y": 0},
         "displacement": {"x": 0, "y": 0}, "acceleration": {"x": 0, "y": 0}},
    ]}
    envelope, kind = _window_from_log(json.dumps(raw), max_frames=4)
    assert kind == "v1"
    assert "samples" in envelope


def test_single_v2_contract_passes_through():
    window = {"schema_version": 2, "source": "fish_vision", "frames": [_FRAME, _FRAME]}
    envelope, kind = _window_from_log(json.dumps(window), max_frames=4)
    assert kind == "v2"
    assert envelope == window
    assert len(envelope["frames"]) == 2


def test_ndjson_contract_line_flattens_into_frames():
    window = {"schema_version": 2, "source": "fish_vision", "frames": [_FRAME] * 3}
    log = "\n".join([json.dumps(window), json.dumps(_FRAME)]) + "\n"
    envelope, kind = _window_from_log(log, max_frames=4)
    assert kind == "v2"
    assert len(envelope["frames"]) == 4


def test_inbox_picks_latest_vision_log(tmp_path):
    from server import config

    config.INBOX_DIR = tmp_path
    config.WATCH_INBOX = True
    old = config.VISION_FRAMES
    config.VISION_FRAMES = 2
    try:
        (tmp_path / "fish_log.json").write_text(_ndjson([_FRAME] * 5), encoding="utf-8")
        data = collect_fish()
        assert data["schema_version"] == 2
        assert len(data["frames"]) == 2
    finally:
        config.VISION_FRAMES = old
        config.WATCH_INBOX = False


def test_malformed_source_rejected():
    from server.collector import _validate

    raw = {"samples": [{"__import__('os').system('true')": 1}]}
    with pytest.raises(SchemaError):
        _validate(raw, "test")


def test_malformed_vision_frame_rejected():
    from server.collector import _validate

    raw = {"timestamp": float("nan"), "fish_count": 0, "activity_pct": 0.0, "fish": []}
    with pytest.raises(SchemaError):
        _validate(raw, "test")