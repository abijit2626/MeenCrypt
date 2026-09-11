"""FISHRAND - cryptography module for the TinkerHub Makeathon.

A deliberately ridiculous physical observation pipeline (a fish, and a
microphone listening to its tank), wrapped in a genuinely careful
RSA-OAEP hybrid encryption scheme.

        Fish observations            ESP32 mic audio
              |                             |
              +--------- fish quality ------+
                         decides which
                              |
                              v
              SHA-256 conditioning -> observation digest
                              |
        HKDF(fresh OS CSPRNG secret, digest) -> AES-256 key
                              |
                    AES-256-GCM encrypt
                              |
              RSA-OAEP wrap the key (public key)
                              |
                    ciphertext + wrapped key

SECURITY MODEL:
    The fish/audio values are NOT cryptographically secure randomness and
    are NOT secret - they condition the key and are recorded as audit
    metadata. The OS CSPRNG provides every message's secret, and the RSA
    private key is the only thing that can ever decrypt. This library does
    not pretend otherwise.

Public API (see api.py):
    encrypt_with_observation(fish, plaintext, public_key=..., audio_observations=...)
    encrypt_with_rsa_hybrid(fish, plaintext, public_key=...)
    decrypt_rsa_hybrid_package(package, private_key=...)
"""

from __future__ import annotations

from .api import (
    OBSERVATION_MODE_AUDIO,
    OBSERVATION_MODE_FISH,
    OBSERVATION_MODE_FISH_AUDIO,
    AuthenticationFailure,
    FishrandError,
    decrypt_rsa_hybrid_package,
    derive_key,
    encrypt_with_observation,
    encrypt_with_rsa_hybrid,
)
from .canonicalize import canonical_bytes
from .schema import (
    AUDIO_SCHEMA_VERSION,
    AUDIO_SOURCE_IDENTIFIER,
    SCHEMA_VERSION,
    SOURCE_IDENTIFIER,
    TRACK_SCHEMA_VERSION,
    SchemaError,
    canonicalize_observations,
    load_observations,
    validate_observations,
)
from .package import load_package, save_package
from .quality import Quality, classify_fish_quality
from .rsa_hybrid import (
    KeyUnwrapError,
    PrivateKeyNotFound,
    generate_keypair,
    load_private_key,
    load_private_key_from_pem,
    load_public_key,
    serialize_private_key,
    serialize_public_key,
)

__version__ = "2.0.0"

__all__ = [
    "__version__",
    "AuthenticationFailure",
    "FishrandError",
    "SCHEMA_VERSION",
    "TRACK_SCHEMA_VERSION",
    "SOURCE_IDENTIFIER",
    "AUDIO_SCHEMA_VERSION",
    "AUDIO_SOURCE_IDENTIFIER",
    "SchemaError",
    "canonical_bytes",
    "canonicalize_observations",
    "load_observations",
    "validate_observations",
    "derive_key",
    "encrypt_with_rsa_hybrid",
    "encrypt_with_observation",
    "decrypt_rsa_hybrid_package",
    "OBSERVATION_MODE_FISH",
    "OBSERVATION_MODE_FISH_AUDIO",
    "OBSERVATION_MODE_AUDIO",
    "load_package",
    "save_package",
    "Quality",
    "classify_fish_quality",
    "KeyUnwrapError",
    "PrivateKeyNotFound",
    "generate_keypair",
    "load_private_key",
    "load_private_key_from_pem",
    "load_public_key",
    "serialize_private_key",
    "serialize_public_key",
]
