import secrets

import pytest

from fishrand.crypto import InvalidTag, canonical_aad, decrypt, encrypt
from fishrand.mixing import RSA_HYBRID_INFO, KEY_BITS, derive_key
from fishrand import cspng


class TestCSPRNG:
    def test_os_random_32(self):
        a = cspng.session_random_32()
        b = cspng.session_random_32()
        assert len(a) == 32
        assert a != b  # CSPRNG, overwhelmingly likely distinct

    def test_nonce_12(self):
        assert len(cspng.aead_nonce_12()) == 12

    def test_never_python_random(self):
        # Ensure the wrappers route through secrets, not random.
        import random

        assert cspng.os_random_bytes.__module__ == "fishrand.cspng"
        assert cspng.session_random_32() != random.randbytes(32)


class TestKeyDerivation:
    def test_derive_key_32_bytes(self):
        key = derive_key(secrets.token_bytes(32), secrets.token_bytes(32))
        assert len(key) == 32

    def test_derive_key_is_pseudo_random(self):
        digest = secrets.token_bytes(32)
        osa = secrets.token_bytes(32)
        osb = secrets.token_bytes(32)
        assert derive_key(digest, osa) != derive_key(digest, osb)

    def test_derive_key_domain_separation(self):
        digest = secrets.token_bytes(32)
        osr = secrets.token_bytes(32)
        a = derive_key(digest, osr, info=RSA_HYBRID_INFO)
        b = derive_key(digest, osr, info="something-else")
        assert a != b

    def test_derive_key_rejects_bad_digest_size(self):
        with pytest.raises(ValueError):
            derive_key(b"short", secrets.token_bytes(32))


class TestAESGCM:
    def test_roundtrip(self):
        key = derive_key(secrets.token_bytes(32), secrets.token_bytes(32))
        nonce = secrets.token_bytes(12)
        aad = canonical_aad()
        ct = encrypt(key, nonce, b"i am a secret of a fish", aad)
        assert encrypt(key, nonce, b"i am a secret of a fish", aad) == ct
        assert decrypt(key, nonce, ct, aad) == b"i am a secret of a fish"

    def test_tampered_ciphertext_rejected(self):
        key = derive_key(secrets.token_bytes(32), secrets.token_bytes(32))
        nonce = secrets.token_bytes(12)
        aad = canonical_aad(purpose="FISHRAND-DEMO")
        ct = bytearray(encrypt(key, nonce, b"attack at dawn", aad))
        ct[len(ct) // 2] ^= 0x01  # flip one bit
        with pytest.raises(InvalidTag):
            decrypt(key, nonce, bytes(ct), aad)

    def test_tampered_aad_rejected(self):
        key = derive_key(secrets.token_bytes(32), secrets.token_bytes(32))
        nonce = secrets.token_bytes(12)
        ct = encrypt(key, nonce, b"hello", canonical_aad())
        with pytest.raises(InvalidTag):
            decrypt(key, nonce, ct, canonical_aad(purpose="TAMPERED"))

    def test_wrong_key_rejected(self):
        key = derive_key(secrets.token_bytes(32), secrets.token_bytes(32))
        nonce = secrets.token_bytes(12)
        ct = encrypt(key, nonce, b"hello", canonical_aad())
        wrong_key = derive_key(secrets.token_bytes(32), secrets.token_bytes(32))
        with pytest.raises(InvalidTag):
            decrypt(wrong_key, nonce, ct, canonical_aad())

    def test_nonce_size_enforced(self):
        key = secrets.token_bytes(32)
        with pytest.raises(ValueError):
            encrypt(key, b"short", b"x")
        with pytest.raises(ValueError):
            decrypt(key, b"short", b"x")

    def test_gcm_authenticates_metadata(self):
        # AAD must be bound to the ciphertext.
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        ct = encrypt(key, nonce, b"payload", b"meta-v1")
        assert decrypt(key, nonce, ct, b"meta-v1") == b"payload"
        with pytest.raises(InvalidTag):
            decrypt(key, nonce, ct, b"meta-v2")