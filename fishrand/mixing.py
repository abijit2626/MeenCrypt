"""PARTS 6-7 - CRYPTOGRAPHIC MIXING AND KEY DERIVATION.

Combines:
    1. an observation digest (fish, fish+audio, or audio - see
       fishrand/entropy.py). Physical, public, NOT secret: it conditions
       the key and records what the tank looked like, nothing more.
    2. the trusted secret - fresh per-message OS CSPRNG bytes
       (cspng.session_random_32), which is what actually makes the key
       unguessable. The derived AES key is then wrapped with the RSA
       public key (fishrand/rsa_hybrid.py) and the raw secret discarded.

using the standard HKDF-SHA256 construction - never a hand-rolled mixing
formula. Domain separation is enforced with an explicit `info` string so
key material derived here can never be reused for an unrelated purpose.

    AES-256 key = HKDF-SHA256(
        input_key_material = secret || observation_digest,
        info               = "FISHRAND-AES256-GCM-v4-rsa-hybrid",
    )
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KDF_NAME = "HKDF-SHA256"
KEY_SIZE_BYTES = 32  # AES-256
KEY_BITS = KEY_SIZE_BYTES * 8

# The only domain-separation string: the RSA-OAEP hybrid pipeline is the
# only encryption path. It covers every fish-quality mode (fish-only,
# fish+audio, audio-only) - the secret is a fresh CSPRNG value per
# message, so the modes never need separate domains to stay independent.
RSA_HYBRID_INFO = "FISHRAND-AES256-GCM-v4-rsa-hybrid"


def derive_key(observation_digest: bytes, secret: bytes, *, info: str = RSA_HYBRID_INFO) -> bytes:
    """Derive a 32-byte AES-256 key via HKDF-SHA256.

    Args:
        observation_digest: 32-byte SHA-256 digest of the canonical
                    observation stream - fish, audio, or the two combined
                    (see fishrand/entropy.py). Public conditioning input,
                    never a secret and never a decryption gate.
        secret:     the trusted secret component - fresh OS CSPRNG bytes
                    for this message (cspng.session_random_32).
        info:       explicit domain-separation string.

    Returns:
        32 bytes of key material. Never log or display it.
    """
    if len(observation_digest) != 32:
        raise ValueError(f"observation_digest must be 32 bytes, got {len(observation_digest)}")
    if not secret:
        raise ValueError("secret must not be empty")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE_BYTES,
        salt=None,
        info=info.encode("utf-8"),
    )
    return hkdf.derive(secret + observation_digest)
