"""Tests for the RSA-OAEP hybrid pipeline (package version 4) - the only
encryption path fishrand supports.

Same VALID fish fixture and tamper-detection pattern throughout: decode
via parse_package(), flip a byte, rebuild the b64 field, expect rejection.
"""

from __future__ import annotations

import base64
import inspect
import json
import pathlib
import stat as stat_module
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli as fishrand_cli  # noqa: E402  (needs REPO_ROOT on sys.path first)

from fishrand import (  # noqa: E402
    AuthenticationFailure,
    decrypt_rsa_hybrid_package,
    encrypt_with_rsa_hybrid,
)
from fishrand.package import build_package, parse_package  # noqa: E402
from fishrand.rsa_hybrid import (  # noqa: E402
    RSA_KEY_SIZE,
    RSA_PUBLIC_EXPONENT,
    PrivateKeyNotFound,
    generate_keypair,
    load_private_key,
    load_public_key,
    serialize_private_key,
    serialize_public_key,
    unwrap_session_key,
    wrap_session_key,
)

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

DIARY = b"BUY MILK, EGGS AND MAGGI"

# GOOD-quality fish window (v2 frames, >=2 fish/>=5% activity - see
# fishrand/quality.py) for the CLI roundtrip tests below: cli.py's `encrypt`
# always goes through the adaptive encrypt_with_observation() pipeline
# (see cli.py cmd_encrypt), which requires audio_observations whenever the
# fish quality classifies below GOOD. Using a GOOD window here keeps those
# tests audio-free and focused on the RSA keygen/encrypt/decrypt path.
CLI_GOOD_FISH = {
    "schema_version": 2,
    "source": "fish_vision",
    "frames": [
        {"timestamp": 1750000000.0, "fish_count": 2, "activity_pct": 20.0, "fish": [
            {"id": 0, "centroid": [10.0, 20.0], "area": 100.0, "speed": 1.0, "direction_rad": 0.0},
            {"id": 1, "centroid": [30.0, 40.0], "area": 90.0, "speed": 1.5, "direction_rad": 0.1},
        ]},
    ],
}


@pytest.fixture(scope="module")
def keypair():
    return generate_keypair()


@pytest.fixture(scope="module")
def other_keypair():
    return generate_keypair()


class TestKeyGeneration:
    def test_key_size_and_exponent(self, keypair):
        private_key, public_key = keypair
        assert private_key.key_size == RSA_KEY_SIZE == 3072
        assert public_key.public_numbers().e == RSA_PUBLIC_EXPONENT == 65537

    def test_wrap_unwrap_roundtrip(self, keypair):
        private_key, public_key = keypair
        aes_key = b"\x01" * 32
        wrapped = wrap_session_key(public_key, aes_key)
        assert wrapped != aes_key
        assert unwrap_session_key(private_key, wrapped) == aes_key

    def test_pem_roundtrip_no_passphrase(self, keypair, tmp_path):
        private_key, public_key = keypair
        priv_path = tmp_path / "private_key.pem"
        pub_path = tmp_path / "public_key.pem"
        priv_path.write_bytes(serialize_private_key(private_key, None))
        pub_path.write_bytes(serialize_public_key(public_key))
        loaded_priv = load_private_key(priv_path)
        loaded_pub = load_public_key(pub_path)
        aes_key = b"\x02" * 32
        wrapped = wrap_session_key(loaded_pub, aes_key)
        assert unwrap_session_key(loaded_priv, wrapped) == aes_key

    def test_pem_roundtrip_with_passphrase(self, keypair, tmp_path):
        private_key, _ = keypair
        priv_path = tmp_path / "private_key.pem"
        priv_path.write_bytes(serialize_private_key(private_key, b"correct horse"))
        loaded = load_private_key(priv_path, b"correct horse")
        assert loaded.key_size == RSA_KEY_SIZE

    def test_passphrase_required_when_missing(self, keypair, tmp_path):
        private_key, _ = keypair
        priv_path = tmp_path / "private_key.pem"
        priv_path.write_bytes(serialize_private_key(private_key, b"correct horse"))
        with pytest.raises(ValueError):
            load_private_key(priv_path, None)

    def test_wrong_passphrase_rejected(self, keypair, tmp_path):
        private_key, _ = keypair
        priv_path = tmp_path / "private_key.pem"
        priv_path.write_bytes(serialize_private_key(private_key, b"correct horse"))
        with pytest.raises(ValueError):
            load_private_key(priv_path, b"wrong passphrase")

    def test_missing_private_key_file_raises_and_creates_nothing(self, tmp_path):
        missing = tmp_path / "does_not_exist.pem"
        with pytest.raises(PrivateKeyNotFound):
            load_private_key(missing)
        assert not missing.exists()

    def test_load_private_key_from_pem_roundtrip(self, keypair):
        from fishrand.rsa_hybrid import load_private_key_from_pem

        private_key, public_key = keypair
        pem_text = serialize_private_key(private_key, None).decode("utf-8")
        loaded = load_private_key_from_pem(pem_text)
        aes_key = b"\x03" * 32
        wrapped = wrap_session_key(public_key, aes_key)
        assert unwrap_session_key(loaded, wrapped) == aes_key

    def test_load_private_key_from_pem_never_touches_disk(self, keypair, tmp_path, monkeypatch):
        from fishrand.rsa_hybrid import load_private_key_from_pem

        private_key, _ = keypair
        pem_text = serialize_private_key(private_key, None).decode("utf-8")
        monkeypatch.chdir(tmp_path)
        load_private_key_from_pem(pem_text)
        assert list(tmp_path.iterdir()) == []

    def test_load_private_key_from_pem_wrong_passphrase_rejected(self, keypair):
        from fishrand.rsa_hybrid import load_private_key_from_pem

        private_key, _ = keypair
        pem_text = serialize_private_key(private_key, b"correct horse")
        with pytest.raises(ValueError):
            load_private_key_from_pem(pem_text, b"wrong passphrase")


class TestKeygenFilePermissions:
    def test_private_key_written_0600_by_cli_keygen(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        keys_dir = tmp_path / "keys"
        assert fishrand_cli.main(["keygen", "--output", str(keys_dir), "--no-passphrase"]) == 0
        priv_path = keys_dir / "private_key.pem"
        assert priv_path.exists()
        assert stat_module.S_IMODE(priv_path.stat().st_mode) == 0o600


class TestAADRename:
    """AAD_PURPOSE moved from "FISHRAND-DEMO" to "FISHRAND-DIARY-V1" - a
    normal encrypt/decrypt roundtrip must still succeed with the new
    default, and packages encrypted under the old purpose string must
    still fail (proving the AAD is actually authenticated, not decorative).
    """

    def test_default_aad_purpose_is_diary_v1(self):
        from fishrand.crypto import AAD_PURPOSE, canonical_aad

        assert AAD_PURPOSE == "FISHRAND-DIARY-V1"
        assert b"FISHRAND-DIARY-V1" in canonical_aad()

    def test_roundtrip_survives_the_rename(self, keypair):
        private_key, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        assert decrypt_rsa_hybrid_package(pkg, private_key=private_key) == DIARY

    def test_old_purpose_string_no_longer_verifies(self, keypair):
        from fishrand.crypto import canonical_aad

        private_key, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        decoded = parse_package(pkg)
        assert decoded["aad"] != canonical_aad(purpose="FISHRAND-DEMO")


class TestHybridRoundtrip:
    def test_encrypt_decrypt_roundtrip_without_fish_at_decrypt(self, keypair):
        private_key, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        assert pkg["version"] == 4
        assert decrypt_rsa_hybrid_package(pkg, private_key=private_key) == DIARY

    def test_decrypt_signature_takes_no_fish_argument(self):
        params = list(inspect.signature(decrypt_rsa_hybrid_package).parameters)
        assert "fish_observations" not in params

    def test_package_fields(self, keypair):
        _, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, "hello", public_key=public_key)
        assert pkg["key_algorithm"] == "RSA-OAEP-SHA256"
        assert pkg["rsa_key_size"] == 3072
        assert "encrypted_session_key_b64" in pkg
        assert "os_random_b64" not in pkg
        assert "fish_commitment_b64" not in pkg
        assert "fish_hash" in pkg  # retained as audit metadata only

    def test_wrong_private_key_rejected(self, keypair, other_keypair):
        _, public_key = keypair
        wrong_private_key, _ = other_keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        with pytest.raises(AuthenticationFailure):
            decrypt_rsa_hybrid_package(pkg, private_key=wrong_private_key)

    def test_fresh_nonce_and_session_key_each_time(self, keypair):
        _, public_key = keypair
        pkg_a = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        pkg_b = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        assert pkg_a["nonce_b64"] != pkg_b["nonce_b64"]
        assert pkg_a["encrypted_session_key_b64"] != pkg_b["encrypted_session_key_b64"]
        assert pkg_a["payload_b64"] != pkg_b["payload_b64"]

    def test_fish_repetition_does_not_weaken_key(self, keypair):
        """Same fish window across two encryptions still yields distinct
        ciphertexts/session keys - the fresh OS CSPRNG dominates, not the
        (possibly idle/repeated) fish observations."""
        _, public_key = keypair
        pkg_a = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        pkg_b = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        assert pkg_a["fish_hash"] == pkg_b["fish_hash"]
        assert pkg_a["payload_b64"] != pkg_b["payload_b64"]

    def test_minimal_fish_dataset_handled_safely(self, keypair):
        private_key, public_key = keypair
        minimal = {"schema_version": 1, "source": "fish_vision", "samples": [VALID["samples"][0]]}
        pkg = encrypt_with_rsa_hybrid(minimal, DIARY, public_key=public_key)
        assert decrypt_rsa_hybrid_package(pkg, private_key=private_key) == DIARY


class TestTamperDetection:
    def test_tampered_ciphertext_raises(self, keypair):
        private_key, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        decoded = parse_package(pkg)
        blob = bytearray(decoded["payload"])
        blob[len(blob) // 2] ^= 0x01
        pkg["payload_b64"] = base64.b64encode(bytes(blob)).decode()
        with pytest.raises(AuthenticationFailure):
            decrypt_rsa_hybrid_package(pkg, private_key=private_key)

    def test_tampered_nonce_raises(self, keypair):
        private_key, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        decoded = parse_package(pkg)
        nonce = bytearray(decoded["nonce"])
        nonce[0] ^= 0x01
        pkg["nonce_b64"] = base64.b64encode(bytes(nonce)).decode()
        with pytest.raises(AuthenticationFailure):
            decrypt_rsa_hybrid_package(pkg, private_key=private_key)

    def test_tampered_aad_raises(self, keypair):
        private_key, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        decoded = parse_package(pkg)
        aad = bytearray(decoded["aad"])
        aad[-1] = ord("!") if aad[-1] != ord("!") else ord("?")
        pkg["aad_b64"] = base64.b64encode(bytes(aad)).decode()
        with pytest.raises(AuthenticationFailure):
            decrypt_rsa_hybrid_package(pkg, private_key=private_key)

    def test_tampered_encrypted_session_key_raises(self, keypair):
        private_key, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        decoded = parse_package(pkg)
        wrapped = bytearray(decoded["encrypted_session_key"])
        wrapped[0] ^= 0x01
        pkg["encrypted_session_key_b64"] = base64.b64encode(bytes(wrapped)).decode()
        with pytest.raises(AuthenticationFailure):
            decrypt_rsa_hybrid_package(pkg, private_key=private_key)


class TestPackageValidation:
    def test_v4_requires_encrypted_session_key(self, keypair):
        _, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        del pkg["encrypted_session_key_b64"]
        with pytest.raises(ValueError):
            parse_package(pkg)

    def test_v4_rejects_unsupported_version(self, keypair):
        _, public_key = keypair
        pkg = encrypt_with_rsa_hybrid(VALID, DIARY, public_key=public_key)
        pkg["version"] = 1
        with pytest.raises(ValueError):
            parse_package(pkg)

    def test_build_package_requires_all_v4_fields(self):
        with pytest.raises(TypeError):
            build_package(
                fish_hash="a" * 64,
                nonce=b"\x00" * 12,
                ciphertext_blob=b"\x00" * 16,
                aad=b"{}",
                kdf_context="x",
                # encrypted_session_key / key_algorithm / rsa_key_size omitted
            )


class TestEvents:
    def test_pipeline_emits_expected_steps(self, keypair):
        private_key, public_key = keypair
        events: list[str] = []
        pkg = encrypt_with_rsa_hybrid(
            VALID, DIARY, public_key=public_key,
            emit=lambda *a: events.append(a[0]),
        )
        for step in (
            "validation", "canonicalization", "conditioning",
            "os_csprng", "kdf", "aes_gcm", "rsa_wrap", "package",
        ):
            assert step in events

        dec_events: list[tuple] = []
        decrypt_rsa_hybrid_package(
            pkg, private_key=private_key,
            emit=lambda *a: dec_events.append((a[0], a[1])),
        )
        assert ("rsa_unwrap", "ok") in dec_events
        assert ("decrypt", "complete") in dec_events


class TestCLIRoundtrip:
    def test_keygen_encrypt_decrypt_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        keys_dir = tmp_path / "keys"
        fish_path = tmp_path / "fish.json"
        fish_path.write_text(json.dumps(CLI_GOOD_FISH), encoding="utf-8")
        out_path = tmp_path / "encrypted.pkg"

        assert fishrand_cli.main(["keygen", "--output", str(keys_dir), "--no-passphrase"]) == 0
        assert (keys_dir / "private_key.pem").exists()
        assert (keys_dir / "public_key.pem").exists()

        rc = fishrand_cli.main([
            "encrypt", "--fish", str(fish_path), "--text", "BUY MILK, EGGS AND MAGGI",
            "--public-key", str(keys_dir / "public_key.pem"), "--out", str(out_path),
        ])
        assert rc == 0
        pkg = json.loads(out_path.read_text(encoding="utf-8"))
        assert pkg["version"] == 4

        decrypted_path = tmp_path / "decrypted.txt"
        rc = fishrand_cli.main([
            "decrypt", "--pkg", str(out_path),
            "--private-key", str(keys_dir / "private_key.pem"),
            "--out", str(decrypted_path),
        ])
        assert rc == 0
        assert decrypted_path.read_text(encoding="utf-8") == "BUY MILK, EGGS AND MAGGI"

    def test_decrypt_missing_private_key_fails_safely(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        keys_dir = tmp_path / "keys"
        fish_path = tmp_path / "fish.json"
        fish_path.write_text(json.dumps(CLI_GOOD_FISH), encoding="utf-8")
        out_path = tmp_path / "encrypted.pkg"

        fishrand_cli.main(["keygen", "--output", str(keys_dir), "--no-passphrase"])
        fishrand_cli.main([
            "encrypt", "--fish", str(fish_path), "--text", "secret",
            "--public-key", str(keys_dir / "public_key.pem"), "--out", str(out_path),
        ])

        missing_key = tmp_path / "usb_removed" / "private_key.pem"
        rc = fishrand_cli.main(["decrypt", "--pkg", str(out_path), "--private-key", str(missing_key)])
        assert rc == 1
        assert not missing_key.exists()
