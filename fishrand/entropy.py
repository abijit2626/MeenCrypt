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