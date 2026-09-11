"""FISHRAND - cryptography / entropy module for the TinkerHub Makeathon.

A deliberately ridiculous physical entropy infrastructure based around a
fish, wrapped in a genuinely careful cryptographic pipeline.

        Fish observations
              ↓
        Validation
              ↓
        Canonical serialization
              ↓
        SHA-256 conditioning → fish_digest
              │                │
              └────────┬───────┘
                       ↓
           HKDF(fish_digest, OS CSPRNG) → AES-256 key
                       ↓
                 AES-256-GCM encrypt
                       ↓
                 ciphertext + tag

SECURITY MODEL:
    The fish-derived values are NOT cryptographically secure randomness.
    The OS CSPRNG (secrets.token_bytes) is and remains the trusted random
    foundation. This library does not pretend otherwise.

Public API (see api.py):
    encrypt_with_fish_entropy(fish_observations, plaintext) -> package
    decrypt_package(fish_observations, package)            -> bytes
    derive_key(fish_json), encrypt_message(key, pt), decrypt_message(key, pkg)
"""

from __future__ import annotations

from .api import (
    AuthenticationFailure,
    FishrandError,
    decrypt_message,
    decrypt_package,
    derive_key,
    encrypt_message,
    encrypt_with_fish_entropy,
)
from .canonicalize import canonical_bytes
from .schema import (
    SCHEMA_VERSION,
    SOURCE_IDENTIFIER,
    TRACK_SCHEMA_VERSION,
    SchemaError,
    canonicalize_observations,
    load_observations,
    validate_observations,
)
from .package import load_package, save_package
from .fishchain import fish_chain, fish_noise, fish_units, verify_commitment

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "AuthenticationFailure",
    "FishrandError",
    "SCHEMA_VERSION",
    "TRACK_SCHEMA_VERSION",
    "SOURCE_IDENTIFIER",
    "SchemaError",
    "canonical_bytes",
    "canonicalize_observations",
    "load_observations",
    "validate_observations",
    "derive_key",
    "encrypt_message",
    "decrypt_message",
    "encrypt_with_fish_entropy",
    "decrypt_package",
    "load_package",
    "save_package",
    "fish_chain",
    "fish_noise",
    "fish_units",
    "verify_commitment",
]