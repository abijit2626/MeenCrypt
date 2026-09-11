"""PARTS 10, 11, 13 - HIGH-LEVEL ENCRYPTION API (RSA-OAEP hybrid only).

    encrypt_with_rsa_hybrid(fish_observations, plaintext, public_key=...)
    encrypt_with_observation(fish, plaintext, public_key=..., audio_observations=...)
    decrypt_rsa_hybrid_package(package, private_key=...)

KEY LIFETIME:
    Every encryption creates a FRESH cryptographic session:
      - a fresh AES-256-GCM nonce (12 bytes, CSPRNG)
      - a fresh 32-byte CSPRNG session secret, discarded once the derived
        AES key has been wrapped with the RSA public key
    Nothing about the secret is stable across messages; the RSA keypair is
    the only long-lived material.

SECURITY MODEL:
    fish/audio data = physical observation (conditions the key, recorded
                as audit metadata; NOT secret, NOT a decryption gate)
    RSA private key = the one and only secret needed to decrypt. The
                public key only encrypts, so it is safe to keep on the
                server/ship with the app.

ADAPTIVE OBSERVATION (encrypt_with_observation):
    The fish-quality classifier decides which physical stream conditions
    the key - fish alone when the tank is lively, audio alone when the
    camera has nothing usable, both when it is in between. This choice is
    made at ENCRYPT time only; decryption is identical in every case.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .canonicalize import canonical_bytes
from .crypto import ALGORITHM, canonical_aad, decrypt as _gcm_decrypt, encrypt as _gcm_encrypt
from .dashboard import make_emit
from .entropy import audio_digest_bytes, combined_observation_digest, fish_digest_bytes
from .errors import AuthenticationFailure, FishrandError
from .mixing import KDF_NAME, KEY_BITS, RSA_HYBRID_INFO, derive_key
from .observe import count_observations, observation_stats
from .package import build_package, parse_package
from .quality import Quality, classify_fish_quality
from .rsa_hybrid import unwrap_session_key, wrap_session_key
from .schema import validate_observations
from .cspng import aead_nonce_12, session_random_32

OBSERVATION_MODE_FISH = "fish"
OBSERVATION_MODE_FISH_AUDIO = "fish_audio"
OBSERVATION_MODE_AUDIO = "audio"


def encrypt_with_rsa_hybrid(
    fish_observations: dict,
    plaintext: str | bytes,
    *,
    public_key: Any,
    emit: Callable[..., Any] | None = None,
) -> dict:
    """Fish-only RSA-OAEP hybrid pipeline (package version 4).

        validate -> canonicalize -> SHA-256 (audit-only fish_hash)
        + fresh OS CSPRNG -> HKDF-SHA256 -> AES-256 session key
        -> AES-256-GCM encrypt
        -> RSA-OAEP wrap the session key with `public_key`
        -> v4 package

    The fish digest NEVER contributes to secrecy or gates decryption - it
    is folded into the KDF purely for domain-separated conditioning, and
    is otherwise just audit metadata on the package. The real secret is
    the fresh 32-byte OS CSPRNG output, which is discarded the moment the
    RSA-wrapped session key is computed - it is never stored.

    Returns the package dict. `emit` (if provided) receives each step
    event for CLI/dashboard streaming.
    """
    if emit is None:
        emit = make_emit()

    _t = time.perf_counter()
    data = validate_observations(fish_observations)
    stats = observation_stats(data)
    emit("validation", "ok", (time.perf_counter() - _t) * 1000, stats.to_dict())

    _t = time.perf_counter()
    canonical = canonical_bytes(data)
    emit(
        "canonicalization",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {"canonical_bytes": len(canonical), "utf8": "yes"},
    )

    _t = time.perf_counter()
    fish_digest = fish_digest_bytes(data)
    emit(
        "conditioning",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {
            "algorithm": "SHA-256",
            "fish_digest": fish_digest.hex(),
            "note": "conditioning/audit metadata only - never a decryption gate",
        },
    )

    return _seal(
        digest=fish_digest,
        plaintext=plaintext,
        public_key=public_key,
        fish_hash=fish_digest.hex(),
        metadata={
            "source": data.get("source"),
            "units": count_observations(data),
            "observation_mode": OBSERVATION_MODE_FISH,
        },
        emit=emit,
    )


def encrypt_with_observation(
    fish_observations: dict,
    plaintext: str | bytes,
    *,
    public_key: Any,
    audio_observations: dict | None = None,
    quality_thresholds: dict | None = None,
    emit: Callable[..., Any] | None = None,
) -> dict:
    """Adaptive RSA-OAEP hybrid pipeline - fish quality picks the digest.

        GOOD   -> fish alone conditions the key (delegates to
                  encrypt_with_rsa_hybrid unchanged).
        MEDIUM -> fish AND audio: SHA256(domain_tag || fish_digest ||
                  audio_digest) conditions the key.
        BAD    -> audio alone conditions the key (fish tracking was
                  unusable, so it is recorded but not used for keying).

    Every mode produces the same v4 package and decrypts identically -
    with the RSA private key alone, no fish/audio needed. fish_hash,
    audio_hash, fish_quality and observation_mode are recorded as audit
    metadata describing the tank at encryption time.

    `quality_thresholds` is forwarded as kwargs to classify_fish_quality().

    Raises ValueError if fish quality is MEDIUM/BAD but audio_observations
    is None - the mic is what compensates for unreliable fish, so a silent
    fish-only fallback would quietly ignore the classifier's verdict.
    """
    if emit is None:
        emit = make_emit()

    validated_fish = validate_observations(fish_observations)
    quality: Quality = classify_fish_quality(validated_fish, **(quality_thresholds or {}))
    emit("fish_quality", "ok", None, {"quality": quality})

    if quality == "GOOD":
        package = encrypt_with_rsa_hybrid(
            fish_observations, plaintext, public_key=public_key, emit=emit
        )
        package["metadata"]["fish_quality"] = quality
        return package

    if audio_observations is None:
        raise ValueError(
            f"fish quality is {quality}: audio_observations is required "
            "(connect the ESP32 mic, or improve fish tracking to reach GOOD)"
        )

    validated_audio = validate_observations(audio_observations)
    fish_digest = fish_digest_bytes(validated_fish)
    audio_digest = audio_digest_bytes(validated_audio)
    emit(
        "conditioning",
        "ok",
        None,
        {
            "algorithm": "SHA-256",
            "fish_digest": fish_digest.hex(),
            "audio_digest": audio_digest.hex(),
            "note": "conditioning/audit metadata only - never a decryption gate",
        },
    )

    if quality == "MEDIUM":
        digest = combined_observation_digest(fish_digest, audio_digest)
        mode = OBSERVATION_MODE_FISH_AUDIO
    else:  # BAD
        digest = audio_digest
        mode = OBSERVATION_MODE_AUDIO

    package = _seal(
        digest=digest,
        plaintext=plaintext,
        public_key=public_key,
        fish_hash=fish_digest.hex(),
        metadata={
            "source": validated_fish.get("source"),
            "units": count_observations(validated_fish),
            "fish_quality": quality,
            "observation_mode": mode,
            "audio_hash": audio_digest.hex(),
        },
        emit=emit,
    )
    return package


def _seal(
    *,
    digest: bytes,
    plaintext: str | bytes,
    public_key: Any,
    fish_hash: str,
    metadata: dict,
    emit: Callable[..., Any],
) -> dict:
    """Shared tail of both encrypt paths: fresh secret -> HKDF -> AES-GCM
    -> RSA-OAEP wrap -> v4 package. `digest` is whichever observation
    digest conditions this message's key."""
    _t = time.perf_counter()
    secret = session_random_32()
    emit(
        "os_csprng",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {"source": "secrets.token_bytes(32)", "bytes": len(secret), "secret": "HIDDEN"},
    )

    _t = time.perf_counter()
    key = derive_key(digest, secret, info=RSA_HYBRID_INFO)
    emit(
        "kdf",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {"kdf": KDF_NAME, "info": RSA_HYBRID_INFO, "key_bits": KEY_BITS, "key_material": "HIDDEN"},
    )

    payload = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    nonce = aead_nonce_12()
    aad = canonical_aad()
    _t = time.perf_counter()
    blob = _gcm_encrypt(key, nonce, payload, aad)
    emit(
        "aes_gcm",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {
            "algorithm": ALGORITHM,
            "nonce_hex": nonce.hex(),
            "ciphertext_bytes": len(blob),
            "tag": "included (16 bytes)",
            "aad": "authenticated metadata",
        },
    )

    _t = time.perf_counter()
    wrapped_key = wrap_session_key(public_key, key)
    emit(
        "rsa_wrap",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {
            "algorithm": "RSA-OAEP-SHA256",
            "rsa_key_size": public_key.key_size,
            "wrapped_key_bytes": len(wrapped_key),
            "key_material": "HIDDEN",
        },
    )

    package = build_package(
        fish_hash=fish_hash,
        nonce=nonce,
        ciphertext_blob=blob,
        aad=aad,
        kdf_context=RSA_HYBRID_INFO,
        encrypted_session_key=wrapped_key,
        key_algorithm="RSA-OAEP-SHA256",
        rsa_key_size=public_key.key_size,
        metadata={"key_bits": KEY_BITS, "key_material": "HIDDEN", **metadata},
    )
    emit("package", "complete", None, {
        "version": package["version"],
        "algorithm": package["algorithm"],
        "observation_mode": package["metadata"].get("observation_mode"),
    })
    return package


def decrypt_rsa_hybrid_package(
    package: dict, *, private_key: Any, emit: Callable[..., Any] | None = None
) -> bytes:
    """Decrypt a v4 RSA-OAEP hybrid package.

    Only the RSA private key is required - no fish observations, no audio,
    no shared code, regardless of which observation mode created the
    package. Raises AuthenticationFailure (via KeyUnwrapError, or on an
    AES-GCM tag failure) on any mismatch; plaintext is never returned on
    failure.
    """
    if emit is None:
        emit = make_emit()

    parsed = parse_package(package)
    if parsed["version"] != 4:
        raise FishrandError(
            f"decrypt_rsa_hybrid_package requires a v4 package, got version {parsed['version']}"
        )

    _t = time.perf_counter()
    key = unwrap_session_key(private_key, parsed["encrypted_session_key"])
    emit(
        "rsa_unwrap",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {"algorithm": parsed["key_algorithm"], "key_material": "HIDDEN"},
    )

    _t = time.perf_counter()
    try:
        plaintext = _gcm_decrypt(key, parsed["nonce"], parsed["payload"], parsed["aad"])
    except Exception as exc:
        emit("aes_gcm", "error", (time.perf_counter() - _t) * 1000, {
            "reason": "AUTHENTICATION FAILED",
            "detail": "AES-256-GCM tag did not verify (tampered or wrong key)",
        })
        raise AuthenticationFailure("AES-256-GCM authentication failed (data tampered or key mismatch)") from exc

    emit("aes_gcm", "ok", (time.perf_counter() - _t) * 1000, {
        "algorithm": parsed["algorithm"],
        "authenticated": "yes",
        "plaintext_bytes": len(plaintext),
    })
    emit("decrypt", "complete", None, {"result": "plaintext released"})
    return plaintext


__all__ = [
    "FishrandError",
    "AuthenticationFailure",
    "OBSERVATION_MODE_FISH",
    "OBSERVATION_MODE_FISH_AUDIO",
    "OBSERVATION_MODE_AUDIO",
    "derive_key",
    "encrypt_with_rsa_hybrid",
    "encrypt_with_observation",
    "decrypt_rsa_hybrid_package",
]
