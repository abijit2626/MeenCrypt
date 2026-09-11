"""PART 4 - FISH ENTROPY CONDITIONING.

Produces the fixed-size fish_digest from the canonical observation bytes.

IMPORTANT WARNING (see spec):
    The SHA-256 digest is CONDITIONING / COMPRESSION, not a magic entropy
    generator. It maps the entire physical observation stream into a fixed
    size. The digest is NOT a "secure random number" and does NOT carry a
    guaranteed 256 bits of entropy just because SHA-256 outputs 32 bytes.

    The fish contribution is one input to mixing; the OS CSPRNG carries
    the real secrecy (see mixing.py).
"""

from __future__ import annotations

import hashlib

from .canonicalize import canonical_bytes
from .schema import validate_observations


def fish_digest_bytes(validated: dict) -> bytes:
    """SHA-256 of the canonical observation stream (32 bytes)."""
    return hashlib.sha256(canonical_bytes(validated)).digest()


def fish_digest_hex(validated: dict) -> str:
    """Hex digest for display/logging."""
    return fish_digest_bytes(validated).hex()


def condition_observations(data: dict) -> bytes:
    """Full Part 1->Part 4 chain: validate, canonicalize, digest.

    Returns the 32-byte fish_digest.
    """
    return fish_digest_bytes(validate_observations(data))


def audio_digest_bytes(validated: dict) -> bytes:
    """SHA-256 of the canonical audio observation stream (32 bytes).

    Reuses the same canonical_bytes() dispatch as fish_digest_bytes() - the
    audio ('readings') shape is just a third branch of that dispatch, so no
    audio-specific hashing logic is needed. Same conditioning warning
    applies: this is compression, not an entropy source.
    """
    return hashlib.sha256(canonical_bytes(validated)).digest()


def audio_digest_hex(validated: dict) -> str:
    """Hex digest for display/logging."""
    return audio_digest_bytes(validated).hex()


# Domain separator for combining fish_digest + audio_digest into one
# observation_digest (MEDIUM fish-quality mode: fish + audio together).
# Distinct from any mixing.py *_INFO string - this tag separates the
# *combination* of two digests from their later use as HKDF input.
OBSERVATION_DOMAIN_TAG = b"FISHRAND-observation-combine-v1"


def combined_observation_digest(fish_digest: bytes, audio_digest: bytes) -> bytes:
    """Domain-separated combination of a fish_digest and an audio_digest.

    combined = SHA-256(OBSERVATION_DOMAIN_TAG || fish_digest || audio_digest)

    Used only for MEDIUM fish-quality sessions (fish + audio together).
    Like fish_digest/audio_digest, this is conditioning/binding material,
    never a secret - the USB code remains the only secret.
    """
    if len(fish_digest) != 32:
        raise ValueError(f"fish_digest must be 32 bytes, got {len(fish_digest)}")
    if len(audio_digest) != 32:
        raise ValueError(f"audio_digest must be 32 bytes, got {len(audio_digest)}")
    return hashlib.sha256(OBSERVATION_DOMAIN_TAG + fish_digest + audio_digest).digest()