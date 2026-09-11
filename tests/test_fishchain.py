"""Tests for the v3 fish-chain key schedule (fishrand/fishchain.py)."""

from __future__ import annotations

import struct

import pytest

from fishrand.cspng import generate_usb_code
from fishrand.fishchain import fish_chain, fish_noise, fish_units, verify_commitment
from fishrand.schema import validate_observations

V2_WINDOW = {
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

V1_WINDOW = {
    "schema_version": 1,
    "source": "fish_vision",
    "samples": [
        {"timestamp_ns": 500000000, "position": {"x": 1.0, "y": 2.0},
         "displacement": {"x": 0.1, "y": 0.0}, "acceleration": {"x": 0.0, "y": 0.1}},
        {"timestamp_ns": 500100000, "position": {"x": 2.0, "y": 2.0},
         "displacement": {"x": 1.0, "y": 0.0}, "acceleration": {"x": 0.9, "y": 0.0}},
    ],
}


def _code_bytes(text: str = "ab" * 32) -> bytes:
    from fishrand.cspng import usb_code_bytes
    return usb_code_bytes(text)


class TestFishUnits:
    def test_v2_units_match_frame_count(self):
        data = validate_observations(V2_WINDOW)
        assert len(fish_units(data)) == 2

    def test_v1_units_match_sample_count(self):
        data = validate_observations(V1_WINDOW)
        assert len(fish_units(data)) == 2

    def test_units_are_bytes(self):
        data = validate_observations(V2_WINDOW)
        for unit in fish_units(data):
            assert isinstance(unit, bytes)


class TestFishNoise:
    def test_v2_noise_non_empty(self):
        data = validate_observations(V2_WINDOW)
        n = fish_noise(data)
        assert isinstance(n, bytes)
        assert len(n) > 0

    def test_v1_noise_non_empty(self):
        data = validate_observations(V1_WINDOW)
        n = fish_noise(data)
        assert isinstance(n, bytes)
        assert len(n) > 0

    def test_noise_deterministic(self):
        import json, copy
        a = fish_noise(validate_observations(V2_WINDOW))
        b = fish_noise(validate_observations(json.loads(json.dumps(V2_WINDOW))))
        assert a == b

    def test_noise_changes_on_fish_mutation(self):
        import json, copy
        data = validate_observations(V2_WINDOW)
        n0 = fish_noise(data)
        mutated = json.loads(json.dumps(V2_WINDOW))
        mutated["frames"][0]["fish"][0]["speed"] = 99.0
        n1 = fish_noise(validate_observations(mutated))
        assert n0 != n1

    def test_noise_changes_on_timestamp_mutation(self):
        import json
        data = validate_observations(V2_WINDOW)
        n0 = fish_noise(data)
        mutated = json.loads(json.dumps(V2_WINDOW))
        mutated["frames"][1]["timestamp"] = 1750000010.0
        n1 = fish_noise(validate_observations(mutated))
        assert n0 != n1


class TestFishChain:
    def test_deterministic(self):
        units = fish_units(validate_observations(V2_WINDOW))
        noise = fish_noise(validate_observations(V2_WINDOW))
        code = _code_bytes()
        k1, c1 = fish_chain(code, units, noise=noise)
        k2, c2 = fish_chain(code, units, noise=noise)
        assert k1 == k2
        assert c1 == c2

    def test_key_is_32_bytes(self):
        key, commit = fish_chain(
            _code_bytes(), fish_units(validate_observations(V2_WINDOW)),
            noise=fish_noise(validate_observations(V2_WINDOW)),
        )
        assert len(key) == 32
        assert len(commit) == 32

    def test_wrong_frame_differs(self):
        code = _code_bytes()
        data1 = validate_observations(V2_WINDOW)
        data2 = validate_observations({**V2_WINDOW, "frames": [
            {**V2_WINDOW["frames"][0], "activity_pct": 99.9},
            V2_WINDOW["frames"][1],
        ]})
        k1, c1 = fish_chain(code, fish_units(data1), noise=fish_noise(data1))
        k2, c2 = fish_chain(code, fish_units(data2), noise=fish_noise(data2))
        assert k1 != k2
        assert c1 != c2

    def test_wrong_code_differs(self):
        data = validate_observations(V2_WINDOW)
        units = fish_units(data)
        noise = fish_noise(data)
        k1, c1 = fish_chain(_code_bytes("ab" * 32), units, noise=noise)
        k2, c2 = fish_chain(_code_bytes("cd" * 32), units, noise=noise)
        assert k1 != k2
        assert c1 != c2

    def test_empty_units_rejected(self):
        with pytest.raises(ValueError, match="no units"):
            fish_chain(_code_bytes(), [], noise=b"")

    def test_extreme_timestamp_delta_does_not_crash(self):
        """Regression: v1 timestamp deltas near schema.py's allowed range
        used to overflow struct.pack(">q") (signed 64-bit) inside
        fish_noise(). The schema-allowed range goes up to ~1.76e19, which
        exceeds the signed-64-bit max (~9.22e18)."""
        from fishrand.schema import TIMESTAMP_NS_MAX, TIMESTAMP_NS_MIN

        extreme = {
            "schema_version": 1,
            "source": "fish_vision",
            "samples": [
                {"timestamp_ns": TIMESTAMP_NS_MIN, "position": {"x": 0.0, "y": 0.0},
                 "displacement": {"x": 0.0, "y": 0.0}, "acceleration": {"x": 0.0, "y": 0.0}},
                {"timestamp_ns": TIMESTAMP_NS_MAX, "position": {"x": 0.0, "y": 0.0},
                 "displacement": {"x": 0.0, "y": 0.0}, "acceleration": {"x": 0.0, "y": 0.0}},
            ],
        }
        data = validate_observations(extreme)
        noise = fish_noise(data)  # must not raise struct.error
        assert isinstance(noise, bytes) and len(noise) > 0
        code = _code_bytes()
        key, commit = fish_chain(code, fish_units(data), noise=noise)
        assert len(key) == 32 and len(commit) == 32


class TestVerifyCommitment:
    def test_valid_passes(self):
        code = _code_bytes()
        data = validate_observations(V2_WINDOW)
        units, noise = fish_units(data), fish_noise(data)
        _, commit = fish_chain(code, units, noise=noise)
        assert verify_commitment(code, units, noise, commit) is True

    def test_wrong_window_fails(self):
        code = _code_bytes()
        data = validate_observations(V2_WINDOW)
        units, noise = fish_units(data), fish_noise(data)
        _, commit = fish_chain(code, units, noise=noise)
        data2 = validate_observations({**V2_WINDOW, "frames": [
            {**V2_WINDOW["frames"][0], "activity_pct": 99.9},
            V2_WINDOW["frames"][1],
        ]})
        assert verify_commitment(code, fish_units(data2), fish_noise(data2), commit) is False

    def test_wrong_code_fails(self):
        data = validate_observations(V2_WINDOW)
        units, noise = fish_units(data), fish_noise(data)
        _, commit = fish_chain(_code_bytes("ab" * 32), units, noise=noise)
        assert verify_commitment(_code_bytes("cd" * 32), units, noise, commit) is False
