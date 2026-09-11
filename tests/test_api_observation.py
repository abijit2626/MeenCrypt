"""Tests for the three-mode observation pipeline (fishrand/api.py
encrypt_with_observation / decrypt_observation_package).

GOOD fish quality -> delegates unchanged to encrypt_with_fish_entropy /
decrypt_package (the existing v1-v4 pipeline, untouched). MEDIUM -> fish +
audio combined into a v5 "fish_audio" package. BAD -> audio alone into a
v5 "audio" package.
"""

from __future__ import annotations

import copy
import json

import pytest

from fishrand import AuthenticationFailure, ObservationModeMismatch
from fishrand.api import decrypt_observation_package, encrypt_with_observation
from fishrand.cspng import generate_usb_code
from fishrand.entropy import audio_digest_bytes, fish_digest_bytes

DIARY = b"the fish and the mic guard this entry"

# fish_count=2, activity_pct=20.0 in every frame -> classify_fish_quality
# defaults (>=2 fish, >=5% activity) call this GOOD.
FISH_GOOD = {
    "schema_version": 2,
    "source": "fish_vision",
    "frames": [
        {"timestamp": 1750000000.0, "fish_count": 2, "activity_pct": 20.0, "fish": [
            {"id": 0, "centroid": [10.0, 20.0], "area": 100.0, "speed": 1.0, "direction_rad": 0.0},
            {"id": 1, "centroid": [30.0, 40.0], "area": 90.0, "speed": 1.5, "direction_rad": 0.1},
        ]},
    ],
}

# fish_count=1, activity_pct=4.0/5.0 -> MEDIUM (>=1 fish/>=1% activity, but
# below GOOD's 2-fish/5% bar).
FISH_MEDIUM = {
    "schema_version": 2,
    "source": "fish_vision",
    "frames": [
        {"timestamp": 1750000000.0, "fish_count": 1, "activity_pct": 4.0,
         "fish": [{"id": 0, "centroid": [10.0, 20.0], "area": 100.0, "speed": 1.0, "direction_rad": 0.0}]},
        {"timestamp": 1750000005.0, "fish_count": 1, "activity_pct": 5.0,
         "fish": [{"id": 0, "centroid": [12.0, 21.0], "area": 110.0, "speed": 2.0, "direction_rad": 0.4}]},
    ],
}

# No fish detected at all -> BAD (schema-valid, but no usable signal).
FISH_BAD = {
    "schema_version": 2,
    "source": "fish_vision",
    "frames": [
        {"timestamp": 1750000000.0, "fish_count": 0, "activity_pct": 0.0, "fish": []},
    ],
}

AUDIO = {
    "schema_version": 1,
    "source": "esp32_mic",
    "window_duration_s": 5.0,
    "readings": [
        {"offset_s": 0.0, "value": 12043},
        {"offset_s": 1.1, "value": 12310},
        {"offset_s": 2.2, "value": 11920},
    ],
}


def _code():
    return generate_usb_code()


# --- GOOD: delegates to the untouched fish-only path ------------------


class TestGoodModeDelegates:
    def test_good_without_audio_produces_fish_chain_package(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_GOOD, DIARY, session_code=code)
        assert pkg["version"] == 3  # v3 fish-chain, exactly like encrypt_with_fish_entropy alone
        assert "observation_mode" not in pkg  # not a v5 package
        assert pkg["metadata"]["fish_quality"] == "GOOD"

    def test_good_roundtrip(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_GOOD, DIARY, session_code=code)
        assert decrypt_observation_package(pkg, fish_observations=FISH_GOOD, session_code=code) == DIARY

    def test_good_ignores_audio_even_if_given(self):
        # Per spec: audio is NOT used as observation input in GOOD mode.
        code = _code()
        pkg_with_audio = encrypt_with_observation(FISH_GOOD, DIARY, audio_observations=AUDIO, session_code=code)
        pkg_without_audio = encrypt_with_observation(FISH_GOOD, DIARY, session_code=code)
        # Both are fish-only v3 packages; audio never entered the KDF, so
        # neither package embeds it.
        assert "audio_observations" not in pkg_with_audio["metadata"]
        assert pkg_with_audio["version"] == pkg_without_audio["version"] == 3


# --- MEDIUM: fish + audio combined -------------------------------------


class TestMediumModeFishAudio:
    def test_produces_v5_fish_audio_package(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_MEDIUM, DIARY, audio_observations=AUDIO, session_code=code)
        assert pkg["version"] == 5
        assert pkg["observation_mode"] == "fish_audio"
        assert pkg["fish_hash"] == fish_digest_bytes(FISH_MEDIUM).hex()
        assert pkg["audio_hash"] == audio_digest_bytes(AUDIO).hex()
        assert pkg["metadata"]["fish_quality"] == "MEDIUM"

    def test_roundtrip(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_MEDIUM, DIARY, audio_observations=AUDIO, session_code=code)
        out = decrypt_observation_package(pkg, fish_observations=FISH_MEDIUM, audio_observations=AUDIO, session_code=code)
        assert out == DIARY

    def test_missing_audio_at_encrypt_raises_value_error(self):
        with pytest.raises(ValueError):
            encrypt_with_observation(FISH_MEDIUM, DIARY, session_code=_code())

    def test_missing_session_code_at_encrypt_raises_value_error(self):
        with pytest.raises(ValueError):
            encrypt_with_observation(FISH_MEDIUM, DIARY, audio_observations=AUDIO)

    def test_missing_fish_at_decrypt_raises_mode_mismatch(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_MEDIUM, DIARY, audio_observations=AUDIO, session_code=code)
        with pytest.raises(ObservationModeMismatch):
            decrypt_observation_package(pkg, audio_observations=AUDIO, session_code=code)

    def test_missing_audio_at_decrypt_raises_mode_mismatch(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_MEDIUM, DIARY, audio_observations=AUDIO, session_code=code)
        with pytest.raises(ObservationModeMismatch):
            decrypt_observation_package(pkg, fish_observations=FISH_MEDIUM, session_code=code)

    def test_tampered_fish_rejected(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_MEDIUM, DIARY, audio_observations=AUDIO, session_code=code)
        tampered_fish = json.loads(json.dumps(FISH_MEDIUM))
        tampered_fish["frames"][0]["activity_pct"] = 99.0
        with pytest.raises(AuthenticationFailure):
            decrypt_observation_package(pkg, fish_observations=tampered_fish, audio_observations=AUDIO, session_code=code)

    def test_tampered_audio_rejected(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_MEDIUM, DIARY, audio_observations=AUDIO, session_code=code)
        tampered_audio = copy.deepcopy(AUDIO)
        tampered_audio["readings"][0]["value"] += 1
        with pytest.raises(AuthenticationFailure):
            decrypt_observation_package(pkg, fish_observations=FISH_MEDIUM, audio_observations=tampered_audio, session_code=code)

    def test_wrong_code_rejected(self):
        pkg = encrypt_with_observation(FISH_MEDIUM, DIARY, audio_observations=AUDIO, session_code=_code())
        with pytest.raises(AuthenticationFailure):
            decrypt_observation_package(pkg, fish_observations=FISH_MEDIUM, audio_observations=AUDIO, session_code=_code())


# --- BAD: audio only -----------------------------------------------------


class TestBadModeAudioOnly:
    def test_produces_v5_audio_package_without_fish_hash(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_BAD, DIARY, audio_observations=AUDIO, session_code=code)
        assert pkg["version"] == 5
        assert pkg["observation_mode"] == "audio"
        assert "fish_hash" not in pkg
        assert pkg["audio_hash"] == audio_digest_bytes(AUDIO).hex()
        assert pkg["metadata"]["fish_quality"] == "BAD"

    def test_roundtrip_without_fish(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_BAD, DIARY, audio_observations=AUDIO, session_code=code)
        out = decrypt_observation_package(pkg, audio_observations=AUDIO, session_code=code)
        assert out == DIARY

    def test_fish_is_never_a_decryption_gate_in_audio_mode(self):
        # Supplying wrong/absent fish must not matter - audio mode never
        # checks fish at all.
        code = _code()
        pkg = encrypt_with_observation(FISH_BAD, DIARY, audio_observations=AUDIO, session_code=code)
        wrong_fish = FISH_GOOD
        out = decrypt_observation_package(pkg, fish_observations=wrong_fish, audio_observations=AUDIO, session_code=code)
        assert out == DIARY

    def test_missing_audio_at_decrypt_raises_mode_mismatch(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_BAD, DIARY, audio_observations=AUDIO, session_code=code)
        with pytest.raises(ObservationModeMismatch):
            decrypt_observation_package(pkg, session_code=code)

    def test_tampered_audio_rejected(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_BAD, DIARY, audio_observations=AUDIO, session_code=code)
        tampered_audio = copy.deepcopy(AUDIO)
        tampered_audio["readings"][0]["value"] += 1
        with pytest.raises(AuthenticationFailure):
            decrypt_observation_package(pkg, audio_observations=tampered_audio, session_code=code)


# --- Cross-mode: wrong observation_mode never silently substituted -------


class TestWrongModeNeverSilentlySwitched:
    def test_audio_pkg_cannot_be_decrypted_as_fish_audio(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_BAD, DIARY, audio_observations=AUDIO, session_code=code)
        # Supplying fish_observations too doesn't change what the package
        # actually needs (its authenticated mode is "audio") - it still
        # succeeds using ONLY audio, fish is just ignored, never required.
        out = decrypt_observation_package(pkg, fish_observations=FISH_GOOD, audio_observations=AUDIO, session_code=code)
        assert out == DIARY

    def test_unrecognized_observation_mode_rejected(self):
        code = _code()
        pkg = encrypt_with_observation(FISH_MEDIUM, DIARY, audio_observations=AUDIO, session_code=code)
        tampered = copy.deepcopy(pkg)
        tampered["observation_mode"] = "bogus"
        with pytest.raises(ValueError):
            # parse_package itself rejects an unrecognized mode before
            # decrypt_observation_package's own dispatch even runs.
            decrypt_observation_package(tampered, fish_observations=FISH_MEDIUM, audio_observations=AUDIO, session_code=code)


# --- Regression: existing v1-v4 fixtures still decrypt via the new entry point ---


class TestBackwardCompatibilityThroughNewEntryPoint:
    def test_v1_demo_package_decrypts_unchanged(self):
        from fishrand.api import encrypt_with_fish_entropy

        pkg = encrypt_with_fish_entropy(FISH_GOOD, DIARY)  # no session_code -> legacy v1 demo
        assert pkg["version"] == 1
        assert decrypt_observation_package(pkg, fish_observations=FISH_GOOD) == DIARY

    def test_v3_fishchain_package_decrypts_unchanged(self):
        from fishrand.api import encrypt_with_fish_entropy

        code = _code()
        pkg = encrypt_with_fish_entropy(FISH_GOOD, DIARY, session_code=code)
        assert pkg["version"] == 3
        assert decrypt_observation_package(pkg, fish_observations=FISH_GOOD, session_code=code) == DIARY

    def test_non_v5_package_without_fish_observations_raises_mode_mismatch(self):
        from fishrand.api import encrypt_with_fish_entropy

        pkg = encrypt_with_fish_entropy(FISH_GOOD, DIARY)
        with pytest.raises(ObservationModeMismatch):
            decrypt_observation_package(pkg)
