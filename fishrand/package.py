"""PART 12 - ENCRYPTED PACKAGE FORMAT (RSA-OAEP hybrid, version 4).

A self-contained JSON envelope carrying everything needed to decrypt with
the matching RSA private key - and nothing else:

    version 4 (RSA-OAEP hybrid): stores NO secret and does NOT need the
    fish/audio window again at decrypt time. The ephemeral AES-256 session
    key is wrapped with an RSA-3072 public key
    (`encrypted_session_key_b64`, see fishrand/rsa_hybrid.py); only the
    matching private key can unwrap it.

`fish_hash` - and the `fish_quality` / `observation_mode` / `audio_hash`
entries the adaptive pipeline puts in `metadata` (see
fishrand/api.py encrypt_with_observation) - are AUDIT information: they
record which physical observations conditioned the key at encryption
time. They are never re-checked and never gate decryption.

SECURITY NOTE:
    The AES key is never stored. Losing the RSA private key means the
    entry is genuinely unrecoverable - that is the point.
"""

from __future__ import annotations

import base64
import datetime
import json
import os

PACKAGE_VERSION = 4
ALGORITHM = "AES-256-GCM"
KDF_NAME = "HKDF-SHA256"

_SECURITY_NOTE = (
    "RSA-OAEP hybrid: the ephemeral AES-256 session key is wrapped with your "
    "RSA-3072 public key. Decryption needs ONLY the matching RSA private key "
    "(kept off this repo, e.g. on a USB drive). fish_hash and the observation "
    "metadata are audit information about the tank at encryption time, never a "
    "decryption gate."
)


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def un_b64(text: str, *, field: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except Exception as exc:  # binascii.Error / ValueError
        raise ValueError(f"{field}: invalid base64") from exc


def build_package(
    *,
    fish_hash: str,
    nonce: bytes,
    ciphertext_blob: bytes,
    aad: bytes,
    kdf_context: str,
    encrypted_session_key: bytes,
    key_algorithm: str,
    rsa_key_size: int,
    metadata: dict | None = None,
) -> dict:
    """Assemble the encrypted package (JSON-safe dict).

    `metadata` is free-form audit information - the adaptive pipeline adds
    `fish_quality`, `observation_mode` and `audio_hash` there. Nothing in
    it is validated or required at decrypt time.
    """
    return {
        "version": PACKAGE_VERSION,
        "algorithm": ALGORITHM,
        "kdf": KDF_NAME,
        "fish_hash": fish_hash,
        "kdf_context": kdf_context,
        "nonce_b64": _b64(nonce),
        "aad_b64": _b64(aad),
        "payload_b64": _b64(ciphertext_blob),  # ciphertext || tag
        "key_algorithm": key_algorithm,
        "rsa_key_size": rsa_key_size,
        "encrypted_session_key_b64": _b64(encrypted_session_key),
        "metadata": {
            **(metadata or {}),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "security_note": _SECURITY_NOTE,
        },
    }


def parse_package(obj: dict) -> dict:
    """Validate + decode a package dict; returns the decoded internal dict.

    Accepts either the serialized (b64) form or an already-decoded dict
    (idempotent). Raises ValueError on malformed structure.
    """
    if not isinstance(obj, dict):
        raise ValueError("package must be a JSON object")

    # Already-decoded internal form (e.g. returned by from_json_text).
    if {"nonce", "aad", "payload"} <= set(obj):
        for field in ("nonce", "aad", "payload"):
            if not isinstance(obj[field], bytes):
                raise ValueError(f"{field}: expected bytes")
        if not isinstance(obj.get("encrypted_session_key"), bytes):
            raise ValueError("encrypted_session_key: expected bytes")
        return obj

    required = {
        "version", "algorithm", "kdf", "fish_hash", "kdf_context",
        "nonce_b64", "aad_b64", "payload_b64",
        "key_algorithm", "rsa_key_size", "encrypted_session_key_b64", "metadata",
    }
    missing = required - set(obj)
    if missing:
        raise ValueError(f"package missing field(s): {sorted(missing)}")
    if obj["version"] != PACKAGE_VERSION or obj["algorithm"] != ALGORITHM:
        raise ValueError("unsupported package version/algorithm")

    return {
        "version": PACKAGE_VERSION,
        "algorithm": obj["algorithm"],
        "kdf": obj["kdf"],
        "fish_hash": str(obj["fish_hash"]),
        "kdf_context": str(obj["kdf_context"]),
        "encrypted_session_key": un_b64(
            str(obj["encrypted_session_key_b64"]), field="encrypted_session_key_b64"
        ),
        "key_algorithm": str(obj["key_algorithm"]),
        "rsa_key_size": int(obj["rsa_key_size"]),
        "nonce": un_b64(str(obj["nonce_b64"]), field="nonce_b64"),
        "aad": un_b64(str(obj["aad_b64"]), field="aad_b64"),
        "payload": un_b64(str(obj["payload_b64"]), field="payload_b64"),
        "metadata": obj["metadata"] if isinstance(obj["metadata"], dict) else {},
    }


def to_json(obj: dict) -> str:
    """Stable JSON text of an assembled package (for files/serialization)."""
    return json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False)


def from_json_text(text: str) -> dict:
    try:
        return parse_package(json.loads(text))
    except json.JSONDecodeError as exc:
        raise ValueError(f"package: invalid JSON: {exc}") from exc


def save_package(package: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(to_json(package))
    # Ciphertext, but still the user's diary - don't leave it world-readable.
    os.chmod(path, 0o600)


def load_package(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return from_json_text(handle.read())
