"""PARTS 10, 11, 13 - HIGH-LEVEL ENCRYPTION API.

    encrypt_with_fish_entropy(fish_observations, plaintext)
    decrypt_package(fish_observations, package)

KEY LIFETIME (Part 11):
    Every encryption creates a FRESH cryptographic session:
      - a fresh AES-256-GCM nonce (12 bytes, CSPRNG)
      - an AES-256 key derived via HKDF-SHA256 for that session
    With a universal USB code the secret is stable across sessions (one code
    unlocks ANY message) - only the nonce is renewed per message.

SECURITY MODEL:
    fish data = physical contribution (conditioned + key-schedule PRF work,
                NOT secret)
    usb code  = the trusted secret, generated once by the OS CSPRNG and
                kept on your USB key (never stored in the package)
    v3        = the fish drives the key schedule (per-unit HMAC-SHA256 chain)
                and signs the window with a public keyed commitment; the code
                is still the ONLY secret.
"""

from __future__ import annotations

import hmac
import time
from typing import Any, Callable

from .canonicalize import canonical_bytes
from .crypto import ALGORITHM, canonical_aad, decrypt as _gcm_decrypt, encrypt as _gcm_encrypt
from .dashboard import make_emit
from .entropy import fish_digest_bytes
from .errors import AuthenticationFailure, FishrandError
from .fishchain import fish_chain, fish_noise, fish_units, verify_commitment
from .mixing import (
    DOMAIN_INFO,
    FISHCHAIN_INFO,
    KDF_NAME,
    KEY_BITS,
    RSA_HYBRID_INFO,
    USB_CODE_INFO,
    derive_key,
)
from .observe import count_observations, observation_stats
from .package import build_package, parse_package
from .rsa_hybrid import unwrap_session_key, wrap_session_key
from .schema import validate_observations
from .cspng import aead_nonce_12, session_random_32, usb_code_bytes


def derive_key_decode(data: dict) -> bytes:
    """Derive the session AES key from fish observations + a session random.

    This is the 'derive_key' primitive exposed at the module boundary.
    """
    validated = validate_observations(data)
    digest = fish_digest_bytes(validated)
    os_random = session_random_32()
    return derive_key(digest, os_random)


def encrypt_message(key: bytes, plaintext: bytes, *, nonce: bytes | None = None) -> dict:
    """Low-level: encrypt with an already-derived key.

    Returns a v2 package dict (no secret embedded, os_random is None). Used
    internally by encrypt_with_fish_entropy and by tests.
    """
    if nonce is None:
        nonce = aead_nonce_12()
    aad = canonical_aad()
    blob = _gcm_encrypt(key, nonce, plaintext, aad)
    h = _sha256_hex(plaintext)
    return build_package(
        fish_hash=h,
        nonce=nonce,
        ciphertext_blob=blob,
        aad=aad,
        os_random=None,
        kdf_context=USB_CODE_INFO,
        metadata={
            "key_bits": KEY_BITS,
            "key_material": "HIDDEN",
        },
    )


def _sha256_hex(payload: bytes) -> str:
    from hashlib import sha256

    return sha256(payload).hexdigest()


def encrypt_with_fish_entropy(
    fish_observations: dict,
    plaintext: str | bytes,
    *,
    session_code: str | None = None,
    emit: Callable[..., Any] | None = None,
) -> dict:
    """Run the full FISHRAND pipeline (spec Parts 1-12):

        validate → canonicalize → SHA-256 → + secret (USB code / CSPRNG)
        → HKDF → AES-GCM → encrypted package

    session_code: the universal USB code (hex string) read from the USB
        key. When given, a v2 package is produced that contains NO secret —
        the code is what unlocks it later. When omitted, the legacy v1
        self-contained demo package is produced (os_random embedded).

    Returns the self-contained package dict. `emit` (if provided) receives
    each step event for dashboard/SSE streaming.
    """
    if emit is None:
        emit = make_emit()

    # --- PART 1: validation ---
    _t = time.perf_counter()
    data = validate_observations(fish_observations)
    stats = observation_stats(data)
    emit("validation", "ok", (time.perf_counter() - _t) * 1000, stats.to_dict())

    # --- PART 2: canonicalization ---
    _t = time.perf_counter()
    canonical = canonical_bytes(data)
    emit(
        "canonicalization",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {"canonical_bytes": len(canonical), "utf8": "yes"},
    )

    # --- PART 4: conditioning ---
    _t = time.perf_counter()
    fish_digest = fish_digest_bytes(data)
    emit(
        "conditioning",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {
            "algorithm": "SHA-256",
            "fish_digest": fish_digest.hex(),
            "note": "conditioning/compression, not an entropy source",
        },
    )

    # --- PART 5: trusted secret (USB universal code / OS CSPRNG) ---
    _t = time.perf_counter()
    if session_code:
        secret = usb_code_bytes(session_code)
        os_random = None  # NOT stored in the package
        emit(
            "os_csprng",
            "ok",
            (time.perf_counter() - _t) * 1000,
            {
                "source": "universal USB code (generated by OS CSPRNG at init)",
                "bytes": len(secret),
                "secret": "HIDDEN",
                "stored_in_package": False,
            },
        )
    else:
        secret = session_random_32()
        os_random = secret  # legacy v1: self-contained demo package
        emit(
            "os_csprng",
            "ok",
            (time.perf_counter() - _t) * 1000,
            {"source": "secrets.token_bytes(32)", "bytes": len(secret), "secret": "HIDDEN"},
        )

    # --- PARTS 6-7: mixing + key derivation ---
    fish_commitment: bytes | None = None
    if not os_random:
        # v3 fish-chain: every fish unit steps a keyed HMAC-SHA256 chain,
        # and the movement noise is extracted into the key. The secret is
        # still ONLY the USB code.
        units = fish_units(data)
        noise = fish_noise(data)
        _t = time.perf_counter()
        key, fish_commitment = fish_chain(secret, units, noise=noise)
        kdf_info = FISHCHAIN_INFO
        emit(
            "fish_chain",
            "ok",
            (time.perf_counter() - _t) * 1000,
            {
                "kdf": "HMAC-SHA256 chain + HKDF-SHA256",
                "info": kdf_info,
                "key_bits": KEY_BITS,
                "prf_units": len(units),
                "hmac_steps": len(units),
                "noise_bytes": len(noise),
                "commitment": fish_commitment.hex(),
                "key_material": "HIDDEN",
                "note": "fish drives the schedule; only the USB code is secret",
            },
        )
    else:
        _t = time.perf_counter()
        key = derive_key(fish_digest, secret, info=DOMAIN_INFO)
        kdf_info = DOMAIN_INFO
        emit(
            "kdf",
            "ok",
            (time.perf_counter() - _t) * 1000,
            {
                "kdf": KDF_NAME,
                "info": kdf_info,
                "key_bits": KEY_BITS,
                "key_material": "HIDDEN",
            },
        )

    # --- PART 5b + 8-9: nonce + AES-256-GCM encryption with AAD ---
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

    # --- PART 12: assemble package ---
    package = build_package(
        fish_hash=fish_digest.hex(),
        nonce=nonce,
        ciphertext_blob=blob,
        aad=aad,
        os_random=os_random,
        kdf_context=kdf_info,
        fish_commitment=fish_commitment,
        metadata={
            "key_bits": KEY_BITS,
            "key_material": "HIDDEN",
            "source": data.get("source"),
            "units": count_observations(data),
        },
    )
    emit("package", "complete", None, {
        "version": package["version"],
        "algorithm": package["algorithm"],
        "fish_commitment": "bound" if fish_commitment else "n/a",
    })
    return package


def decrypt_message(key: bytes, package: dict) -> bytes:
    """Low-level: decrypt with the exact key used at encryption time.

    package accepts an assembled dict or an already-parsed dict. Raises
    AuthenticationFailure if the GCM tag does not verify.
    """
    return _parse_and_decrypt(key, package)


def decrypt_package(
    fish_observations: dict,
    package: dict,
    *,
    session_code: str | None = None,
    emit: Callable[..., Any] | None = None,
) -> bytes:
    """Re-derive the session key from fish data + package and decrypt.

    v1 package (legacy): the secret (os_random) lived inside the package.
    v2 package: the secret is the universal USB code (`session_code`) which
    lives on your USB key and unlocks ANY package; the fish window bound in
    the package supplies the physical component.

    Emits pipeline events (reversed flow) for the dashboard.
    """
    if emit is None:
        emit = make_emit()

    data = validate_observations(fish_observations)
    parsed = parse_package(package)

    digest = fish_digest_bytes(data)
    if not hmac.compare_digest(parsed["fish_hash"], digest.hex()):
        emit("conditioning", "error", None, {"reason": "fish_hash mismatch (different observations)"})
        raise AuthenticationFailure("fish observations do not match the session that created this package")

    if parsed["os_random"] is not None:
        # Legacy v1: the secret was embedded for the self-contained demo.
        # Domain-separation info is the FIXED v1 constant, never read from
        # the untrusted package JSON (kdf_context there is display-only —
        # trusting it here would let a tampered field pick the KDF context).
        _t = time.perf_counter()
        key = derive_key(digest, parsed["os_random"], info=DOMAIN_INFO)
        emit(
            "kdf",
            "ok",
            (time.perf_counter() - _t) * 1000,
            {"kdf": KDF_NAME, "info": DOMAIN_INFO, "key_bits": KEY_BITS, "key_material": "HIDDEN"},
        )
    else:
        if not session_code:
            emit("kdf", "error", None, {
                "reason": "missing universal USB code",
                "detail": "this v2/v3 package requires code.txt from your USB key",
            })
            raise AuthenticationFailure("package needs the universal USB code (code.txt on your USB)")
        secret = usb_code_bytes(session_code)
        if parsed["version"] == 3:
            # v3 fish-chain: re-run the per-unit PRF chain, then verify the
            # public commitment binds this exact fish window + code. Gated
            # strictly on the AUTHENTICATED package version — never on the
            # untrusted kdf_context field, which an attacker can edit
            # without invalidating anything else in the JSON — so a
            # tampered kdf_context can no longer route a v2 package into
            # this branch and skip the commitment check entirely.
            units = fish_units(data)
            noise = fish_noise(data)
            _t = time.perf_counter()
            key, commitment = fish_chain(secret, units, noise=noise)
            if parsed["fish_commitment"] is None or not verify_commitment(
                secret, units, noise, parsed["fish_commitment"]
            ):
                emit("fish_commit", "error", (time.perf_counter() - _t) * 1000, {
                    "reason": "commitment mismatch",
                    "detail": "this window did not create the package (wrong fish or USB code)",
                })
                raise AuthenticationFailure(
                    "fish commitment mismatch: the package was bound to a different window or USB code"
                )
            emit("fish_commit", "ok", (time.perf_counter() - _t) * 1000, {
                "verified": "yes",
                "commitment": commitment.hex(),
            })
            emit(
                "fish_chain",
                "ok",
                0,
                {
                    "kdf": "HMAC-SHA256 chain + HKDF-SHA256",
                    "info": FISHCHAIN_INFO,
                    "key_bits": KEY_BITS,
                    "prf_units": len(units),
                    "noise_bytes": len(noise),
                    "commitment": "verified",
                    "key_material": "HIDDEN",
                },
            )
        else:
            # v2 legacy: HKDF over the USB code + fish digest. Domain
            # separation is the FIXED v2 constant, never read from the
            # untrusted package JSON.
            _t = time.perf_counter()
            key = derive_key(digest, secret, info=USB_CODE_INFO)
            emit(
                "kdf",
                "ok",
                (time.perf_counter() - _t) * 1000,
                {"kdf": KDF_NAME, "info": USB_CODE_INFO, "key_bits": KEY_BITS, "key_material": "HIDDEN"},
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


def _parse_and_decrypt(key: bytes, package: dict) -> bytes:
    parsed = parse_package(package)
    try:
        return _gcm_decrypt(key, parsed["nonce"], parsed["payload"], parsed["aad"])
    except Exception as exc:
        raise AuthenticationFailure("AES-256-GCM authentication failed (data tampered or key mismatch)") from exc


def encrypt_with_rsa_hybrid(
    fish_observations: dict,
    plaintext: str | bytes,
    *,
    public_key: Any,
    emit: Callable[..., Any] | None = None,
) -> dict:
    """RSA-OAEP hybrid pipeline (package version 4).

        validate -> canonicalize -> SHA-256 (audit-only fish_hash)
        + fresh OS CSPRNG -> HKDF-SHA256 -> AES-256 session key
        -> AES-256-GCM encrypt
        -> RSA-OAEP wrap the session key with `public_key`
        -> v4 package

    Unlike encrypt_with_fish_entropy, the fish digest here NEVER
    contributes to secrecy or gates decryption - it is folded into the KDF
    purely for domain-separated conditioning, exactly like the v1 legacy
    path, and is otherwise just audit metadata on the package. The real
    secret for this mode is the fresh 32-byte OS CSPRNG output, which is
    discarded the moment the RSA-wrapped session key is computed - it is
    never stored, unlike v1's `os_random_b64`.

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
            "note": "conditioning/audit metadata only - not a decryption gate in v4",
        },
    )

    _t = time.perf_counter()
    secret = session_random_32()
    emit(
        "os_csprng",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {"source": "secrets.token_bytes(32)", "bytes": len(secret), "secret": "HIDDEN"},
    )

    _t = time.perf_counter()
    key = derive_key(fish_digest, secret, info=RSA_HYBRID_INFO)
    emit(
        "kdf",
        "ok",
        (time.perf_counter() - _t) * 1000,
        {"kdf": KDF_NAME, "info": RSA_HYBRID_INFO, "key_bits": KEY_BITS, "key_material": "HIDDEN"},
    )

    payload = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    nonce = aead_nonce_12()
    aad = canonical_aad(version=4)
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
        fish_hash=fish_digest.hex(),
        nonce=nonce,
        ciphertext_blob=blob,
        aad=aad,
        os_random=None,
        kdf_context=RSA_HYBRID_INFO,
        encrypted_session_key=wrapped_key,
        key_algorithm="RSA-OAEP-SHA256",
        rsa_key_size=public_key.key_size,
        metadata={
            "key_bits": KEY_BITS,
            "key_material": "HIDDEN",
            "source": data.get("source"),
            "units": count_observations(data),
        },
    )
    emit("package", "complete", None, {"version": package["version"], "algorithm": package["algorithm"]})
    return package


def decrypt_rsa_hybrid_package(package: dict, *, private_key: Any, emit: Callable[..., Any] | None = None) -> bytes:
    """Decrypt a v4 RSA-OAEP hybrid package.

    Only the RSA private key is required - no fish observations, no USB
    code. Raises AuthenticationFailure (via KeyUnwrapError, or on an
    AES-GCM tag failure) on any mismatch; plaintext is never returned on
    failure.
    """
    if emit is None:
        emit = make_emit()

    parsed = parse_package(package)
    if parsed["version"] != 4:
        raise FishrandError(f"decrypt_rsa_hybrid_package requires a v4 package, got version {parsed['version']}")

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
    "derive_key",
    "encrypt_message",
    "decrypt_message",
    "encrypt_with_fish_entropy",
    "decrypt_package",
    "encrypt_with_rsa_hybrid",
    "decrypt_rsa_hybrid_package",
]