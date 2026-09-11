"""PART 5B - V3 FISH-CHAIN KEY SCHEDULE + NOISE EXTRACTION.

The v3 story: the fish does the key-schedule legwork. Instead of collapsing
the observation window into one SHA-256 digest in the key path, every fish
frame/sample steps a keyed HMAC-SHA256 chain, and the movement noise is
rinsed through a standard HKDF extractor:

    units   = [frame_bytes(f) or sample_bytes(s)]        per fish unit
    noise   = fish_noise(validated)                       timing deltas +
                                                           raw IEEE754 bytes
    state0  = HMAC(secret, "FISHRAND-v3" ++ key_label)
    state_i = HMAC(secret, state_{i-1} ++ unit_i ++ index_i)   one step/unit
    commit  = HMAC(secret, "FISHRAND-v3" ++ commit_label ++ state_n)
    key     = HKDF-SHA256(ikm = state_n ++ commit ++ noise,
                          info = FISHRAND-AES256-GCM-v3-fishchain)[:32]

`commit` is stored publicly in the package as `fish_commitment`; decrypt
recomputes it, so a wrong fish window (or wrong code) fails loudly BEFORE
any AES work.

HONEST LINE (same as everywhere in FISHRAND):
    The secret is STILL only the USB code. `units` and `noise` are public
    observations - they bind and drive the schedule, they do NOT add
    secrecy. HMAC-SHA256 keyed by the code is a standard secure PRF, so
    this replaces the naive hash-only digest with real per-unit PRF work
    without claiming any extra entropy.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .canonicalize import frame_bytes, sample_bytes
from .mixing import KEY_SIZE_BYTES

FISHCHAIN_LABEL = b"FISHRAND-v3"
_KEY_LABEL = b"\x01"
_COMMIT_LABEL = b"\x02"
UNITS_MAX = 100_000  # PRF chain length guard


def fish_units(validated: dict) -> list[bytes]:
    """Canonical bytes for every fish unit (v2 frames or v1 samples)."""
    if "frames" in validated:
        return [frame_bytes(f) for f in validated["frames"]]
    return [sample_bytes(s) for s in validated["samples"]]


def _float_bytes(value: Any) -> bytes:
    return struct.pack(">d", float(value))


def _noise_v2(frames: list) -> bytes:
    parts: list[bytes] = []
    prev_t: float | None = None
    for frame in frames:
        ts = float(frame["timestamp"])
        if prev_t is not None:
            parts.append(struct.pack(">d", ts - prev_t))
        prev_t = ts
        parts.append(_float_bytes(frame["activity_pct"]))
        for fish in frame["fish"]:
            cx, cy = fish["centroid"]
            parts.append(_float_bytes(cx))
            parts.append(_float_bytes(cy))
            parts.append(_float_bytes(fish["area"]))
            parts.append(_float_bytes(fish["speed"]))
            parts.append(_float_bytes(fish["direction_rad"]))
    return b"".join(parts)


def _noise_v1(samples: list) -> bytes:
    parts: list[bytes] = []
    prev_ns: int | None = None
    for sample in samples:
        ns = int(sample["timestamp_ns"])
        if prev_ns is not None:
            # schema.py allows timestamp_ns up to ~1.76e19, whose deltas can
            # exceed a signed 64-bit range (struct.pack(">q", ...) would
            # raise struct.error). A 16-byte signed integer comfortably
            # covers the full schema-allowed delta range without overflow.
            parts.append((ns - prev_ns).to_bytes(16, "big", signed=True))
        prev_ns = ns
        for axis in ("x", "y"):
            parts.append(_float_bytes(sample["position"][axis]))
            parts.append(_float_bytes(sample["displacement"][axis]))
            parts.append(_float_bytes(sample["acceleration"][axis]))
    return b"".join(parts)


def fish_noise(validated: dict) -> bytes:
    """Deterministic 'movement noise' packed from a validated window.

    Includes inter-frame timing deltas and the raw IEEE754 bytes of every
    movement field. CONDITIONING material only - it is public and adds
    binding, never secrecy.
    """
    if "frames" in validated:
        return _noise_v2(validated["frames"])
    return _noise_v1(validated["samples"])


def fish_chain(secret: bytes, units: list[bytes], *, noise: bytes = b"") -> tuple[bytes, bytes]:
    """Run the v3 fish-chain key schedule.

    Returns (aes_key_256, commitment) where `commitment` is the public,
    code-keyed fingerprint of the exact fish window used.

    Args:
        secret: 32-byte trusted secret (the universal USB code bytes).
        units:  canonical bytes per fish unit (see fish_units()).
        noise:  fish_noise(validated) - folded into the HKDF finalize.
    """
    if not secret:
        raise ValueError("secret must not be empty")
    if not units:
        raise ValueError("fish window has no units (empty samples/frames rejected by schema)")
    if len(units) > UNITS_MAX:
        raise ValueError(f"fish window too large for the PRF chain: {len(units)} units")

    state = hmac.new(secret, FISHCHAIN_LABEL + _KEY_LABEL, hashlib.sha256).digest()
    for i, unit in enumerate(units):
        index = struct.pack(">I", i)
        state = hmac.new(secret, state + unit + index, hashlib.sha256).digest()
    commitment = hmac.new(secret, FISHCHAIN_LABEL + _COMMIT_LABEL + state, hashlib.sha256).digest()

    # Finalize with a standard HKDF extract-and-expand; the fish movement
    # noise is formally extracted here (conditioning, not entropy).
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE_BYTES,
        salt=None,
        info=b"FISHRAND-AES256-GCM-v3-fishchain",
    )
    key = hkdf.derive(commitment + state + noise)
    return key, commitment


def verify_commitment(secret: bytes, units: list[bytes], noise: bytes, expected: bytes) -> bool:
    """Recompute the fish commitment for (secret, units, noise) and compare."""
    recomputed = fish_chain(secret, units, noise=noise)[1]
    return hmac.compare_digest(recomputed, expected)


__all__ = [
    "fish_chain",
    "fish_noise",
    "fish_units",
    "verify_commitment",
    "FISHCHAIN_LABEL",
]