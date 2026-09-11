"""PART 5 - OS CSPRNG.

Trusted randomness from the operating system.

RULE (see spec):
    - Use secrets.token_bytes() (OS-backed CSPRNG).
    - NEVER random.random() / random.randint() / time-based seeds /
      manual PRNGs / custom generators.
"""

from __future__ import annotations

import secrets


def os_random_bytes(n: int = 32) -> bytes:
    """Cryptographically strong random bytes from the OS CSPRNG.

    This is the trusted random component. The fish data never replaces it.
    """
    return secrets.token_bytes(n)


def session_random_32() -> bytes:
    """32 bytes of OS randomness, mixed into key derivation each session."""
    return os_random_bytes(32)


def aead_nonce_12() -> bytes:
    """Fresh 96-bit nonce for one AES-256-GCM operation."""
    return os_random_bytes(12)


def generate_usb_code() -> str:
    """Generate the universal USB code: 32 CSPRNG bytes as hex (64 chars).

    Written once to the USB key (`code.txt`). It is the secret that can
    unlock ANY message encrypted by FISHRAND.
    """
    return secrets.token_hex(32)


def usb_code_bytes(text: str | bytes) -> bytes:
    """Strictly decode a universal USB code (hex) into its 32 key bytes.

    Rejects anything that is not a 64-character lowercase hex string.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="strict")
    if not isinstance(text, str):
        raise ValueError("usb code must be a hex string")
    stripped = text.strip()
    if len(stripped) != 64:
        raise ValueError(f"usb code must be 64 hex chars, got {len(stripped)}")
    try:
        return bytes.fromhex(stripped)
    except ValueError as exc:
        raise ValueError("usb code: invalid hex characters") from exc