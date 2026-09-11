"""PARTS 6-7 - CRYPTOGRAPHIC MIXING AND KEY DERIVATION.

Combines:
    1. fish-derived digest (from entropy/conditioning)
    2. the trusted secret — the universal USB code (v2) or per-session OS
       CSPRNG bytes (v1 demo)

using the standard HKDF-SHA256 construction - never a hand-rolled mixing
formula. Domain separation is enforced with an explicit `info` string so
key material derived here can never be reused for an unrelated purpose.

    AES-256 key = HKDF-SHA256(
        input_key_material = secret || fish_digest,
        info              = "FISHRAND-AES256-GCM-v2-usbcode",
    )
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KDF_NAME = "HKDF-SHA256"
KEY_SIZE_BYTES = 32  # AES-256
KEY_BITS = KEY_SIZE_BYTES * 8
# v1 legacy: per-session OS randomness (stored in the demo package).
DOMAIN_INFO = "FISHRAND-AES256-GCM-v1"
# v2: the universal USB code is the secret (never stored in the package).
USB_CODE_INFO = "FISHRAND-AES256-GCM-v2-usbcode"
# v3: the fish drives the key schedule (per-frame HMAC chain + noise
# extraction). The secret is still the universal USB code.
FISHCHAIN_INFO = "FISHRAND-AES256-GCM-v3-fishchain"


def derive_key(fish_digest: bytes, secret: bytes, *, info: str = DOMAIN_INFO) -> bytes:
    """Derive a 32-byte AES-256 key via HKDF-SHA256.

    Args:
        fish_digest: 32-byte SHA-256 digest of canonical fish observations.
        secret:     the trusted secret component — either the OS CSPRNG
                    bytes (v1 demo) or the universal USB code (v2). The
                    secret is the real secrecy; the fish digest conditions
                    the physical observation stream.
        info:       explicit domain-separation string.

    Returns:
        32 bytes of key material. Never log or display it.
    """
    if len(fish_digest) != 32:
        raise ValueError(f"fish_digest must be 32 bytes, got {len(fish_digest)}")
    if not secret:
        raise ValueError("secret must not be empty")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE_BYTES,
        salt=None,
        info=info.encode("utf-8"),
    )
    return hkdf.derive(secret + fish_digest)