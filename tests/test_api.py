import json

import pytest

from fishrand import (
    AuthenticationFailure,
    decrypt_message,
    decrypt_package,
    derive_key,
    encrypt_message,
    encrypt_with_fish_entropy,
)
from fishrand.cspng import generate_usb_code, session_random_32
from fishrand.entropy import fish_digest_bytes
from fishrand.package import load_package, parse_package, save_package
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

TRACK = {
    "schema_version": 2,
    "source": "fish_vision",
    "frames": [
        {
            "timestamp": 1750000000.0,
            "fish_count": 1,
            "activity_pct": 4.2,
            "fish": [{"id": 0, "centroid": [120.5, 240.2], "area": 320.5, "speed": 3.2, "direction_rad": 0.2}],
        },
        {
            "timestamp": 1750000005.0,
            "fish_count": 1,
            "activity_pct": 2.0,
            "fish": [{"id": 0, "centroid": [125.0, 238.0], "area": 331.0, "speed": 5.1, "direction_rad": -0.4}],
        },
    ],
}

DIARY = b"fish flake coordinates, day 3"


class TestHighLevelRoundtrip:
    def test_encrypt_decrypt_roundtrip(self):
        pkg = encrypt_with_fish_entropy(VALID, DIARY)
        assert pkg["algorithm"] == "AES-256-GCM"
        assert pkg["kdf"] == "HKDF-SHA256"
        assert "os_random_b64" in pkg
        out = decrypt_package(VALID, pkg)
        assert out == DIARY

    def test_package_has_no_aes_key(self):
        pkg = encrypt_with_fish_entropy(VALID, DIARY)
        text = json.dumps(pkg)
        assert "kdf_context" in pkg
        # The raw AES key is never embedded - it can't even be represented.
        assert "aes_key" not in text.lower().replace("aes-256-gcm", "")

    def test_package_roundtrips_through_disk(self, tmp_path):
        pkg = encrypt_with_fish_entropy(VALID, DIARY)
        path = tmp_path / "pkg.json"
        save_package(pkg, path)
        loaded = load_package(path)
        assert decrypt_package(VALID, loaded) == DIARY

    def test_fresh_session_per_encryption(self):
        pkg_a = encrypt_with_fish_entropy(VALID, DIARY)
        pkg_b = encrypt_with_fish_entropy(VALID, DIARY)
        assert pkg_a["nonce_b64"] != pkg_b["nonce_b64"]
        assert pkg_a["os_random_b64"] != pkg_b["os_random_b64"]
        assert pkg_a["payload_b64"] != pkg_b["payload_b64"]


class TestUSBCodeMode:
    def test_code_mode_produces_v3_package_without_secret(self):
        pkg = encrypt_with_fish_entropy(TRACK, DIARY, session_code=generate_usb_code())
        assert pkg["version"] == 3
        assert "os_random_b64" not in pkg
        assert "fish_commitment_b64" in pkg
        assert pkg["kdf_context"] == "FISHRAND-AES256-GCM-v3-fishchain"

    def test_code_roundtrip(self):
        code = generate_usb_code()
        pkg = encrypt_with_fish_entropy(TRACK, DIARY, session_code=code)
        assert decrypt_package(TRACK, pkg, session_code=code) == DIARY

    def test_wrong_code_rejected(self):
        pkg = encrypt_with_fish_entropy(TRACK, DIARY, session_code=generate_usb_code())
        with pytest.raises(AuthenticationFailure):
            decrypt_package(TRACK, pkg, session_code=generate_usb_code())

    def test_missing_code_rejected(self):
        pkg = encrypt_with_fish_entropy(TRACK, DIARY, session_code=generate_usb_code())
        with pytest.raises(AuthenticationFailure):
            decrypt_package(TRACK, pkg)

    def test_code_roundtrip_through_disk(self, tmp_path):
        code = generate_usb_code()
        pkg = encrypt_with_fish_entropy(TRACK, DIARY, session_code=code)
        path = tmp_path / "diary.pkg"
        save_package(pkg, path)
        assert decrypt_package(TRACK, load_package(path), session_code=code) == DIARY

    def test_same_code_unlocks_any_message(self):
        code = generate_usb_code()
        other = json.loads(json.dumps(TRACK))
        other["frames"][0]["activity_pct"] = 9.9
        pkg_a = encrypt_with_fish_entropy(TRACK, DIARY, session_code=code)
        pkg_b = encrypt_with_fish_entropy(other, b"a completely different entry", session_code=code)
        assert decrypt_package(TRACK, pkg_a, session_code=code) == DIARY
        assert decrypt_package(other, pkg_b, session_code=code) == b"a completely different entry"


class TestTamperDetection:
    def test_tampered_ciphertext_raises(self):
        pkg = encrypt_with_fish_entropy(VALID, DIARY)
        decoded = parse_package(pkg)
        blob = bytearray(decoded["payload"])
        blob[len(blob) // 2] ^= 0x01
        pkg["payload_b64"] = __import__("base64").b64encode(bytes(blob)).decode()
        with pytest.raises(AuthenticationFailure):
            decrypt_package(VALID, pkg)

    def test_wrong_fish_observations_raises(self):
        pkg = encrypt_with_fish_entropy(VALID, DIARY)
        wrong = json.loads(json.dumps(VALID))
        wrong["samples"][0]["position"]["x"] += 0.1
        with pytest.raises(AuthenticationFailure):
            decrypt_package(wrong, pkg)

    def test_tampered_aad_raises(self):
        pkg = encrypt_with_fish_entropy(VALID, DIARY)
        decoded = parse_package(pkg)
        aad = bytearray(decoded["aad"])
        aad[-1] = ord("!") if aad[-1] != ord("!") else ord("?")
        pkg["aad_b64"] = __import__("base64").b64encode(bytes(aad)).decode()
        with pytest.raises(AuthenticationFailure):
            decrypt_package(VALID, pkg)

    def test_code_mode_tampered_ciphertext_raises(self):
        code = generate_usb_code()
        pkg = encrypt_with_fish_entropy(TRACK, DIARY, session_code=code)
        decoded = parse_package(pkg)
        blob = bytearray(decoded["payload"])
        blob[len(blob) // 2] ^= 0x01
        pkg["payload_b64"] = __import__("base64").b64encode(bytes(blob)).decode()
        with pytest.raises(AuthenticationFailure):
            decrypt_package(TRACK, pkg, session_code=code)

    def test_tampered_kdf_context_cannot_bypass_v3_commitment_check(self):
        """Regression: decrypt_package() used to route into the v3
        fish-chain branch whenever EITHER version==3 OR the untrusted
        kdf_context field said so. Editing a v2 package's kdf_context to
        the v3 string used to skip the commitment check entirely
        (fish_commitment is None on a v2 package) and even emit a fake
        "verified" event, only failing later at the GCM stage.

        Dispatch must now depend solely on the authenticated `version`
        field, and key derivation must use FIXED per-version constants
        rather than the untrusted kdf_context field — so tampering
        kdf_context has no effect at all: the v2 package still decrypts
        normally, and the v3 commitment-check branch is never entered."""
        from fishrand.cspng import usb_code_bytes
        from fishrand.mixing import USB_CODE_INFO

        code = generate_usb_code()
        # Build a genuine v2 (no-secret, no-commitment) package directly,
        # using the same fixed domain-separation info decrypt_package()'s
        # v2 branch now uses.
        digest = fish_digest_bytes(validate_observations(TRACK))
        key = derive_key(digest, usb_code_bytes(code), info=USB_CODE_INFO)
        pkg = encrypt_message(key, DIARY)
        # encrypt_message() stamps fish_hash=sha256(plaintext) (a low-level
        # helper detail, not the fish digest) — patch it to the real fish
        # digest so decrypt_package()'s fish-hash consistency check passes,
        # matching what encrypt_with_fish_entropy() would have stored.
        pkg["fish_hash"] = digest.hex()
        assert pkg["version"] == 2 and "fish_commitment_b64" not in pkg

        tampered = json.loads(json.dumps(pkg))
        tampered["kdf_context"] = "FISHRAND-AES256-GCM-v3-fishchain"
        events: list[dict] = []
        plaintext = decrypt_package(
            TRACK, tampered, session_code=code,
            emit=lambda *a: events.append({"step": a[0], "detail": a[3] or {}}),
        )
        # The tampered label is fully ignored: decryption still succeeds...
        assert plaintext == DIARY
        # ...and the v3 fish-chain/commitment branch was never entered, so
        # no step could have falsely claimed the commitment "verified".
        assert not any(e["step"] in ("fish_commit", "fish_chain") for e in events)
        assert not any(e["detail"].get("verified") == "yes" for e in events)


class TestLowLevelApi:
    def test_derive_encrypt_decrypt_same_key(self):
        digest = fish_digest_bytes(validate_observations(VALID))
        osr = session_random_32()
        key = derive_key(digest, osr)
        pkg = encrypt_message(key, DIARY)
        assert decrypt_message(key, pkg) == DIARY

    def test_encrypt_message_never_embeds_secret(self):
        key = derive_key(
            fish_digest_bytes(validate_observations(VALID)),
            session_random_32(),
        )
        pkg = encrypt_message(key, DIARY)
        assert pkg["version"] == 2  # v2 package: no secret embedded
        assert "os_random_b64" not in pkg
        assert decrypt_message(key, pkg) == DIARY


class TestEvents:
    def test_pipeline_emits_step_events(self):
        events: list[dict] = []
        pkg = encrypt_with_fish_entropy(
            VALID,
            DIARY,
            emit=lambda *a: events.append({
                "step": a[0],
                "status": a[1],
                "detail": a[3] or {},
            }),
        )
        steps = [e["step"] for e in events]
        assert "validation" in steps
        assert "canonicalization" in steps
        assert "conditioning" in steps
        assert "os_csprng" in steps
        assert "kdf" in steps
        assert "aes_gcm" in steps
        assert "package" in steps
        dec_events: list[dict] = []
        decrypt_package(VALID, pkg, emit=lambda *a: dec_events.append((a[0], a[1])))
        assert ("decrypt", "complete") in dec_events

    def test_code_mode_marks_secret_not_stored(self):
        events: list[dict] = []
        pkg = encrypt_with_fish_entropy(
            TRACK,
            DIARY,
            session_code=generate_usb_code(),
            emit=lambda *a: events.append(a[3] or {}),
        )
        assert pkg["version"] == 3
        assert "fish_commitment_b64" in pkg
        os_event = [e for e in events if e.get("stored_in_package") is False]
        assert os_event
        fish_chain_steps = [e for e in events if "prf_units" in e]
        assert fish_chain_steps[0]["prf_units"] == len(TRACK["frames"])