"""PART 12 - ENCRYPTED PACKAGE FORMAT.

A self-contained JSON envelope that carries everything needed to
identify / reconstruct a session:

  version 1 (LEGACY demo): includes `os_random_b64` so encrypt → save →
    decrypt is fully self-contained (os_random was the true secret — the
    tradeoff documented in older READMEs).

  version 2 (LEGACY USB code): does NOT store any secret. The key is derived
    from the universal USB code combined with the bound fish window. Kept
    decryptable for files created before the v3 fish-chain.

  version 3 (fish-chain): does NOT store any secret, and additionally signs
    the exact fish window with a public, code-keyed `fish_commitment`
    (HMAC-SHA256 chain over every fish unit). The AES key is produced by
    the v3 fish-chain key schedule (see fishrand/fishchain.py). A single
    USB code still unlocks ANY package the fish encrypted.

  version 4 (RSA-OAEP hybrid): does NOT store any secret and does NOT need
    the fish window again at decrypt time. The ephemeral AES-256 session
    key is wrapped with an RSA-3072 public key (`encrypted_session_key_b64`,
    see fishrand/rsa_hybrid.py); only the matching RSA private key can
    unwrap it. `fish_hash` is retained purely as audit metadata here, never
    a decryption gate.

SECURITY NOTE:
    The secret AES key is NEVER stored in the package, and since v2 neither
    is any recoverable randomness. Losing the USB code means the entry is
    genuinely unrecoverable - that is the point.
"""

from __future__ import annotations

import base64
import datetime
import json
from typing import Any

_PACKAGE_VERSIONS = (1, 2, 3, 4)


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
    os_random: bytes | None,
    kdf_context: str,
    fish_commitment: bytes | None = None,
    encrypted_session_key: bytes | None = None,
    key_algorithm: str | None = None,
    rsa_key_size: int | None = None,
    metadata: dict | None = None,
) -> dict:
    """Assemble the encrypted package (JSON-safe dict).

    os_random given          → legacy v1 self-contained demo package.
    os_random=None           → v2 USB-code package (no secret embedded).
    os_random=None + fish_commitment → v3 fish-chain package: no secret,
                             plus a public commitment binding the exact fish
                             window (see fishrand/fishchain.py).
    encrypted_session_key given → v4 RSA-OAEP hybrid package: no secret, no
                             fish_commitment; the AES session key is wrapped
                             for an RSA-3072 public key (see
                             fishrand/rsa_hybrid.py) and fish_hash is
                             audit-only metadata.
    """
    if encrypted_session_key is not None and (os_random is not None or fish_commitment is not None):
        raise ValueError("v4 (RSA hybrid) package must not carry os_random or fish_commitment")
    if encrypted_session_key is not None:
        version = 4
    elif os_random is not None:
        version = 1
    elif fish_commitment is not None:
        version = 3
    else:
        version = 2
    base: dict = {
        "version": version,
        "algorithm": "AES-256-GCM",
        "kdf": "HKDF-SHA256",
        "fish_hash": fish_hash,
        "kdf_context": kdf_context,
        "nonce_b64": _b64(nonce),
        "aad_b64": _b64(aad),
        "payload_b64": _b64(ciphertext_blob),  # ciphertext || tag
    }
    if os_random is not None:
        base["os_random_b64"] = _b64(os_random)
    if fish_commitment is not None:
        base["fish_commitment_b64"] = _b64(fish_commitment)
    if encrypted_session_key is not None:
        base["key_algorithm"] = key_algorithm
        base["rsa_key_size"] = rsa_key_size
        base["encrypted_session_key_b64"] = _b64(encrypted_session_key)
    base["metadata"] = {
        **(metadata or {}),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "security_note": _SECURITY_NOTE[version],
    }
    return base


_SECURITY_NOTE = {
    1: (
        "os_random is included for the self-contained hackathon demo; "
        "see fishrand/package.py SECURITY NOTE (v1 legacy)."
    ),
    2: (
        "v2 legacy: no secret stored in this package; key = HKDF(usb_code || fish_digest) "
        "where usb_code lives only on your USB key."
    ),
    3: (
        "v3 fish-chain: no secret stored. The fish drives the key schedule "
        "(HMAC-SHA256 per fish unit) and signs the package via fish_commitment; "
        "key = fish_chain(usb_code, fish). usb_code lives only on your USB key."
    ),
    4: (
        "v4 RSA-OAEP hybrid: the ephemeral AES-256 session key is wrapped with "
        "your RSA-3072 public key. Decryption needs ONLY the matching RSA "
        "private key (kept off this repo, e.g. on a USB drive) - the fish "
        "window is not required again. fish_hash here is audit metadata only, "
        "never a decryption gate."
    ),
}


def parse_package(obj: dict) -> dict:
    """Validate + decode a package dict; returns decoded internal dict.

    Accepts either the serialized (b64) form or an already-decoded dict
    (idempotent). Raises ValueError on malformed structure. Never touches
    secrets: for v2 packages `os_random` decodes to None - the secret stays
    on the USB key.
    """
    if not isinstance(obj, dict):
        raise ValueError("package must be a JSON object")
    # Already-decoded internal form (e.g. returned by from_json_text).
    if {"nonce", "aad", "payload"} <= set(obj) and "os_random" in obj:
        for field in ("nonce", "aad", "payload"):
            if not isinstance(obj[field], bytes):
                raise ValueError(f"{field}: expected bytes")
        if obj["os_random"] is not None and not isinstance(obj["os_random"], bytes):
            raise ValueError("os_random: expected bytes or None")
        if obj.get("fish_commitment") is not None and not isinstance(obj["fish_commitment"], bytes):
            raise ValueError("fish_commitment: expected bytes or None")
        if obj.get("encrypted_session_key") is not None and not isinstance(obj["encrypted_session_key"], bytes):
            raise ValueError("encrypted_session_key: expected bytes or None")
        return obj
    required = {
        "version", "algorithm", "kdf", "fish_hash", "kdf_context",
        "nonce_b64", "aad_b64", "payload_b64", "metadata",
    }
    missing = required - set(obj)
    if missing:
        raise ValueError(f"package missing field(s): {sorted(missing)}")
    version = obj["version"]
    if version not in _PACKAGE_VERSIONS or obj["algorithm"] != "AES-256-GCM":
        raise ValueError("unsupported package version/algorithm")

    os_random: bytes | None
    if version == 1:
        if "os_random_b64" not in obj:
            raise ValueError("v1 package missing 'os_random_b64'")
        os_random = un_b64(str(obj["os_random_b64"]), field="os_random_b64")
    else:
        if "os_random_b64" in obj:
            raise ValueError("v2/v3/v4 package must not embed a secret ('os_random_b64')")
        os_random = None  # the universal USB code lives outside the package

    fish_commitment: bytes | None
    if version == 3:
        if "fish_commitment_b64" not in obj:
            raise ValueError("v3 package missing 'fish_commitment_b64'")
        fish_commitment = un_b64(str(obj["fish_commitment_b64"]), field="fish_commitment_b64")
    else:
        fish_commitment = None

    encrypted_session_key: bytes | None
    key_algorithm: str | None
    rsa_key_size: int | None
    if version == 4:
        if "encrypted_session_key_b64" not in obj:
            raise ValueError("v4 package missing 'encrypted_session_key_b64'")
        encrypted_session_key = un_b64(str(obj["encrypted_session_key_b64"]), field="encrypted_session_key_b64")
        key_algorithm = str(obj.get("key_algorithm", ""))
        rsa_key_size = int(obj.get("rsa_key_size", 0))
    else:
        if "encrypted_session_key_b64" in obj:
            raise ValueError("only v4 packages carry 'encrypted_session_key_b64'")
        encrypted_session_key = None
        key_algorithm = None
        rsa_key_size = None

    return {
        "version": version,
        "algorithm": obj["algorithm"],
        "kdf": obj["kdf"],
        "fish_hash": str(obj["fish_hash"]),
        "kdf_context": str(obj["kdf_context"]),
        "os_random": os_random,
        "fish_commitment": fish_commitment,
        "encrypted_session_key": encrypted_session_key,
        "key_algorithm": key_algorithm,
        "rsa_key_size": rsa_key_size,
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


def load_package(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return from_json_text(handle.read())