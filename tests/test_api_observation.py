"""Tests for the adaptive observation pipeline (fishrand/api.py
encrypt_with_observation), on top of the RSA-OAEP hybrid scheme (package
version 4) - the only encryption path fishrand supports.

GOOD fish quality -> fish alone conditions the key (delegates to
encrypt_with_rsa_hybrid). MEDIUM -> fish AND audio combined. BAD -> audio
alone. Every mode produces the same v4 package and decrypts identically -
with the RSA private key alone, no fish/audio needed at decrypt time.
"""

from __future__ import annotations

import copy
import json

import pytest

from fishrand import AuthenticationFailure
from fishrand.api import decrypt_rsa_hybrid_package, encrypt_with_observation
from fishrand.entropy import audio_digest_bytes, fish_digest_bytes
from fishrand.rsa_hybrid import generate_keypair

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


@pytest.fixture(scope="module")
def keypair():
    return generate_keypair()


@pytest.fixture(scope="module")
def other_keypair():
    return generate_keypair()


# --- GOOD: fish alone conditions the key --------------------------------


class TestGoodModeFishOnly:
    def test_good_without_audio_produces_v4_package(self, keypair):
        _, public_key = keypair
        pkg = encrypt_with_observation(FISH_GOOD, DIARY, public_key=public_key)
        assert pkg["version"] == 4
        assert pkg["metadata"]["observation_mode"] == "fish"
        assert pkg["metadata"]["fish_quality"] == "GOOD"
        assert pkg["fish_hash"] == fish_digest_bytes(FISH_GOOD).hex()

    def test_good_roundtrip(self, keypair):
        private_key, public_key = keypair
        pkg = encrypt_with_observation(FISH_GOOD, DIARY, public_key=public_key)
        assert decrypt_rsa_hybrid_package(pkg, private_key=private_key) == DIARY

    def test_good_ignores_audio_even_if_given(self, keypair):
        # Per spec: audio is NOT used as observation input in GOOD mode.
        _, public_key = keypair
        pkg_with_audio = encrypt_with_observation(
            FISH_GOOD, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        pkg_without_audio = encrypt_with_observation(FISH_GOOD, DIARY, public_key=public_key)
        assert "audio_hash" not in pkg_with_audio["metadata"]
        assert pkg_with_audio["version"] == pkg_without_audio["version"] == 4
        assert pkg_with_audio["metadata"]["observation_mode"] == "fish"


# --- MEDIUM: fish + audio combined -------------------------------------


class TestMediumModeFishAudio:
    def test_produces_v4_fish_audio_package(self, keypair):
        _, public_key = keypair
        pkg = encrypt_with_observation(
            FISH_MEDIUM, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        assert pkg["version"] == 4
        assert pkg["metadata"]["observation_mode"] == "fish_audio"
        assert pkg["fish_hash"] == fish_digest_bytes(FISH_MEDIUM).hex()
        assert pkg["metadata"]["audio_hash"] == audio_digest_bytes(AUDIO).hex()
        assert pkg["metadata"]["fish_quality"] == "MEDIUM"

    def test_roundtrip(self, keypair):
        private_key, public_key = keypair
        pkg = encrypt_with_observation(
            FISH_MEDIUM, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        assert decrypt_rsa_hybrid_package(pkg, private_key=private_key) == DIARY

    def test_missing_audio_at_encrypt_raises_value_error(self, keypair):
        _, public_key = keypair
        with pytest.raises(ValueError):
            encrypt_with_observation(FISH_MEDIUM, DIARY, public_key=public_key)

    def test_wrong_private_key_rejected(self, keypair, other_keypair):
        _, public_key = keypair
        wrong_private_key, _ = other_keypair
        pkg = encrypt_with_observation(
            FISH_MEDIUM, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        with pytest.raises(AuthenticationFailure):
            decrypt_rsa_hybrid_package(pkg, private_key=wrong_private_key)

    def test_decrypt_needs_no_fish_or_audio(self, keypair):
        """The whole point of the RSA hybrid scheme: decrypting a
        fish+audio-conditioned package needs ONLY the private key."""
        private_key, public_key = keypair
        pkg = encrypt_with_observation(
            FISH_MEDIUM, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        import inspect

        assert "fish_observations" not in inspect.signature(decrypt_rsa_hybrid_package).parameters
        assert "audio_observations" not in inspect.signature(decrypt_rsa_hybrid_package).parameters
        assert decrypt_rsa_hybrid_package(pkg, private_key=private_key) == DIARY


# --- BAD: audio only -----------------------------------------------------


class TestBadModeAudioOnly:
    def test_produces_v4_audio_package(self, keypair):
        _, public_key = keypair
        pkg = encrypt_with_observation(
            FISH_BAD, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        assert pkg["version"] == 4
        assert pkg["metadata"]["observation_mode"] == "audio"
        assert pkg["metadata"]["audio_hash"] == audio_digest_bytes(AUDIO).hex()
        assert pkg["metadata"]["fish_quality"] == "BAD"
        # fish_hash still carries the (unused-for-keying) fish digest as
        # audit metadata - see encrypt_with_observation's docstring.
        assert pkg["fish_hash"] == fish_digest_bytes(FISH_BAD).hex()

    def test_roundtrip_without_fish(self, keypair):
        private_key, public_key = keypair
        pkg = encrypt_with_observation(
            FISH_BAD, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        assert decrypt_rsa_hybrid_package(pkg, private_key=private_key) == DIARY

    def test_missing_audio_at_encrypt_raises_value_error(self, keypair):
        _, public_key = keypair
        with pytest.raises(ValueError):
            encrypt_with_observation(FISH_BAD, DIARY, public_key=public_key)

    def test_tampered_audio_rejected(self, keypair):
        """Audio never gates decryption (only the RSA key does), but it DOES
        condition the key - a package encrypted against one audio window
        cannot be re-derived from a different one at encrypt time (there is
        nothing to "tamper" post-hoc on the decrypt side since decrypt takes
        no audio at all; this instead proves two different audio windows
        yield different ciphertexts for the same plaintext)."""
        _, public_key = keypair
        tampered_audio = copy.deepcopy(AUDIO)
        tampered_audio["readings"][0]["value"] += 1
        pkg_a = encrypt_with_observation(
            FISH_BAD, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        pkg_b = encrypt_with_observation(
            FISH_BAD, DIARY, public_key=public_key, audio_observations=tampered_audio
        )
        assert pkg_a["metadata"]["audio_hash"] != pkg_b["metadata"]["audio_hash"]


# --- Cross-mode: every mode is a plain, interchangeable v4 package -------


class TestEveryModeProducesAnOrdinaryV4Package:
    def test_all_three_modes_decrypt_with_only_the_private_key(self, keypair):
        private_key, public_key = keypair
        pkg_good = encrypt_with_observation(FISH_GOOD, DIARY, public_key=public_key)
        pkg_medium = encrypt_with_observation(
            FISH_MEDIUM, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        pkg_bad = encrypt_with_observation(
            FISH_BAD, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        for pkg in (pkg_good, pkg_medium, pkg_bad):
            assert pkg["version"] == 4
            assert decrypt_rsa_hybrid_package(pkg, private_key=private_key) == DIARY

    def test_tampered_package_rejected_regardless_of_mode(self, keypair):
        import base64

        private_key, public_key = keypair
        pkg = encrypt_with_observation(
            FISH_MEDIUM, DIARY, public_key=public_key, audio_observations=AUDIO
        )
        blob = bytearray(base64.b64decode(pkg["payload_b64"]))
        blob[len(blob) // 2] ^= 0x01
        evil = json.loads(json.dumps(pkg))
        evil["payload_b64"] = base64.b64encode(bytes(blob)).decode()
        with pytest.raises(AuthenticationFailure):
            decrypt_rsa_hybrid_package(evil, private_key=private_key)
