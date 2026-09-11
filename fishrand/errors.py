"""Shared exception hierarchy for the FISHRAND public API.

Lives in its own module (rather than api.py) so other internal modules
(e.g. rsa_hybrid.py) can raise/subclass these without creating an import
cycle with api.py.
"""

from __future__ import annotations


class FishrandError(Exception):
    """Base error for the FISHRAND API."""


class AuthenticationFailure(FishrandError):
    """A cryptographic check failed - AES-GCM tag verification, a fish
    commitment/hash mismatch, or an RSA-OAEP unwrap failure. Data was
    tampered with, or the key/nonce/AAD/commitment do not match. Callers
    must treat the payload as untrusted; plaintext is never returned on
    failure."""


__all__ = ["FishrandError", "AuthenticationFailure"]
