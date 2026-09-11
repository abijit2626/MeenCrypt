"""RSA-OAEP HYBRID KEY WRAPPING (package version 4) - the only scheme.

A long-term RSA-3072 keypair wraps every message's session key:

    observation digest (SHA-256 of fish, fish+audio, or audio -
    conditioning only, never a decryption gate)
            +
    fresh OS CSPRNG bytes (cspng.session_random_32)
            |
            v
      HKDF-SHA256  ->  ephemeral AES-256 session key
            |
    +-------+-------+
    |               |
    v               v
AES-256-GCM     RSA-OAEP wrap(public key)
 encrypt              |
    |                 v
    +---------> encrypted_session_key
            |
            v
      v4 package (see fishrand/package.py)

Decryption needs ONLY the RSA private key + the package - the fish/audio
window is never required again. `fish_hash` (and the fish_quality /
observation_mode / audio_hash entries in the package metadata) are AUDIT
metadata ("this is what the tank looked like at encryption time"); they
are never re-checked at decrypt time and are not a security property.

Key management:
    - RSA-3072, public exponent 65537.
    - RSA-OAEP, MGF1(SHA-256), SHA-256, no label - via the `cryptography`
      library only. No hand-rolled asymmetric crypto.
    - The public key ships with the app; the private key belongs on a
      removable USB drive and is NEVER auto-generated on a missing-key
      decrypt attempt (see PrivateKeyNotFound) - the CLI must fail safely.
    - An ordinary USB drive is NOT a hardware security module: anyone who
      copies private_key.pem can decrypt with it. An optional PEM
      passphrase gives a little at-rest protection, but this remains
      demo-grade key custody, not production-grade.
"""

from __future__ import annotations

import pathlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .errors import AuthenticationFailure, FishrandError

RSA_KEY_SIZE = 3072
RSA_PUBLIC_EXPONENT = 65537
WRAP_ALGORITHM = "RSA-OAEP-SHA256"


class PrivateKeyNotFound(FishrandError):
    """The RSA private key file could not be found or read (e.g. the USB
    drive holding it is not connected). Callers must fail safely and must
    NEVER generate a replacement keypair in response to this error."""


class KeyUnwrapError(AuthenticationFailure):
    """RSA-OAEP unwrapping failed: wrong private key, or a corrupted /
    tampered `encrypted_session_key_b64` field."""


def _oaep_padding() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def generate_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Generate a fresh RSA-3072 keypair via the `cryptography` library."""
    private_key = rsa.generate_private_key(
        public_exponent=RSA_PUBLIC_EXPONENT,
        key_size=RSA_KEY_SIZE,
    )
    return private_key, private_key.public_key()


def serialize_private_key(private_key: rsa.RSAPrivateKey, passphrase: bytes | None = None) -> bytes:
    """PKCS8 PEM. `passphrase` given -> BestAvailableEncryption; else
    NoEncryption() (still just a file on disk - see module docstring)."""
    encryption: serialization.KeySerializationEncryption
    if passphrase:
        encryption = serialization.BestAvailableEncryption(passphrase)
    else:
        encryption = serialization.NoEncryption()
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )


def serialize_public_key(public_key: rsa.RSAPublicKey) -> bytes:
    """SubjectPublicKeyInfo PEM - safe to bundle with the application."""
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _load_private_key_from_data(
    data: bytes, passphrase: bytes | None, *, source_desc: str
) -> rsa.RSAPrivateKey:
    """Parse PEM private-key bytes, wrapping library errors uniformly.

    Shared by load_private_key (from disk) and load_private_key_from_pem
    (in memory) so both report failures identically.
    """
    try:
        key = serialization.load_pem_private_key(data, password=passphrase)
    except TypeError as exc:
        raise ValueError(
            "RSA private key is passphrase-protected; supply the correct passphrase"
        ) from exc
    except ValueError as exc:
        raise ValueError(
            f"RSA private key ({source_desc}) is invalid or the passphrase is wrong: {exc}"
        ) from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError(f"key ({source_desc}) is not an RSA private key")
    return key


def load_private_key(path: str | pathlib.Path, passphrase: bytes | None = None) -> rsa.RSAPrivateKey:
    """Load a PEM RSA private key from `path`.

    Raises PrivateKeyNotFound if the file is missing/unreadable - the
    caller (CLI) must fail safely and must never generate a replacement
    key in response.
    """
    p = pathlib.Path(path)
    if not p.exists():
        raise PrivateKeyNotFound(f"RSA private key not found at {p}. Insert the FISHRAND key USB.")
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise PrivateKeyNotFound(f"RSA private key at {p} could not be read: {exc}") from exc
    return _load_private_key_from_data(data, passphrase, source_desc=str(p))


def load_private_key_from_pem(
    pem_text: str | bytes, passphrase: bytes | None = None
) -> rsa.RSAPrivateKey:
    """Load a PEM RSA private key straight from memory - never touches disk.

    This is what lets the server accept a private key the browser uploads
    for one unlock request without ever persisting it.
    """
    data = pem_text.encode("utf-8") if isinstance(pem_text, str) else pem_text
    return _load_private_key_from_data(data, passphrase, source_desc="in-memory PEM")


def load_public_key(path: str | pathlib.Path) -> rsa.RSAPublicKey:
    """Load a PEM RSA public key from `path`. Public keys ship with the
    app, so a missing file is a plain FileNotFoundError, not a
    PrivateKeyNotFound-style "insert the USB" condition."""
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"RSA public key not found at {p}")
    key = serialization.load_pem_public_key(p.read_bytes())
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError(f"key at {p} is not an RSA public key")
    return key


def wrap_session_key(public_key: rsa.RSAPublicKey, aes_key: bytes) -> bytes:
    """RSA-OAEP encrypt the 32-byte ephemeral AES session key."""
    return public_key.encrypt(aes_key, _oaep_padding())


def unwrap_session_key(private_key: rsa.RSAPrivateKey, wrapped: bytes) -> bytes:
    """RSA-OAEP decrypt back to the 32-byte AES session key.

    Raises KeyUnwrapError on any failure (wrong key, corrupted blob) -
    never returns garbage key material silently.
    """
    try:
        return private_key.decrypt(wrapped, _oaep_padding())
    except ValueError as exc:
        raise KeyUnwrapError("RSA-OAEP unwrap failed: wrong private key or corrupted package") from exc


__all__ = [
    "RSA_KEY_SIZE",
    "RSA_PUBLIC_EXPONENT",
    "WRAP_ALGORITHM",
    "PrivateKeyNotFound",
    "KeyUnwrapError",
    "generate_keypair",
    "serialize_private_key",
    "serialize_public_key",
    "load_private_key",
    "load_private_key_from_pem",
    "load_public_key",
    "wrap_session_key",
    "unwrap_session_key",
]
