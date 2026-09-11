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
    """32 bytes of OS randomness: the per-message session secret.

    This is the ONLY secret in the pipeline. It is mixed into key
    derivation, then the resulting AES key is wrapped with the RSA public
    key (see fishrand/rsa_hybrid.py) and the raw secret is discarded - it
    is never stored anywhere.
    """
    return os_random_bytes(32)


def aead_nonce_12() -> bytes:
    """Fresh 96-bit nonce for one AES-256-GCM operation."""
    return os_random_bytes(12)