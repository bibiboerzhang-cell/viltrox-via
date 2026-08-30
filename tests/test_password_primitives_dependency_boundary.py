from __future__ import annotations

import hashlib

from app.core import passwords
from app.core import security


def test_v2_password_hash_is_stable_for_explicit_salt_and_reexported() -> None:
    salt_hex = "00112233445566778899aabbccddeeff"
    expected_digest = hashlib.pbkdf2_hmac(
        "sha256",
        b"correct horse battery staple",
        bytes.fromhex(salt_hex),
        passwords.PBKDF2_V2_ITERATIONS,
    ).hex()
    expected = f"v2:{salt_hex}:{expected_digest}"

    assert passwords.hash_password("correct horse battery staple", salt_hex) == expected
    assert security.hash_password("correct horse battery staple", salt_hex) == expected
    assert security.hash_password is passwords.hash_password
    assert security.verify_password is passwords.verify_password


def test_password_verification_preserves_v2_and_legacy_contracts() -> None:
    salt_hex = "ffeeddccbbaa99887766554433221100"
    v2 = passwords.hash_password("secret", salt_hex)
    legacy_digest = hashlib.pbkdf2_hmac(
        "sha256",
        b"secret",
        bytes.fromhex(salt_hex),
        passwords.PBKDF2_V1_ITERATIONS,
    ).hex()
    legacy = f"{salt_hex}:{legacy_digest}"

    assert passwords.verify_password("secret", v2) is True
    assert passwords.verify_password("wrong", v2) is False
    assert passwords.verify_password("secret", legacy) is True
    assert passwords.verify_password("wrong", legacy) is False
    assert passwords.needs_password_rehash(v2) is False
    assert passwords.needs_password_rehash(legacy) is True


def test_malformed_password_hashes_fail_closed() -> None:
    for stored in ("", "v2:not-hex:digest", "not-hex:digest", "v2:only-two"):
        assert passwords.verify_password("secret", stored) is False
