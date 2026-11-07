"""Unit tests: password hashing, JWT, password policy."""

from __future__ import annotations

import time

import pytest

from app.core import security
from app.core.errors import AuthenticationError
from app.schemas.auth import validate_password_strength


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        hashed = security.hash_password("correct horse battery staple")
        assert hashed != "correct horse battery staple"
        assert hashed.startswith("$argon2")
        assert security.verify_password(hashed, "correct horse battery staple")

    def test_wrong_password_fails(self):
        hashed = security.hash_password("right password")
        assert not security.verify_password(hashed, "wrong password")

    def test_salts_are_unique(self):
        assert security.hash_password("same") != security.hash_password("same")

    def test_malformed_hash_is_rejected_not_raised(self):
        assert not security.verify_password("not-a-hash", "x")


class TestAccessTokens:
    def _make(self, **overrides):
        import uuid

        claims = dict(
            secret_key="test-secret",
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            role="user",
            ttl_seconds=60,
        )
        claims.update(overrides)
        return security.create_access_token(**claims)

    def test_roundtrip(self):
        token = self._make()
        claims = security.decode_access_token(secret_key="test-secret", token=token)
        assert claims["typ"] == "access"
        assert claims["role"] == "user"
        assert "sub" in claims and "sid" in claims

    def test_expired_token_rejected(self):
        token = self._make(ttl_seconds=-10)
        with pytest.raises(AuthenticationError):
            security.decode_access_token(secret_key="test-secret", token=token)

    def test_tampered_signature_rejected(self):
        token = self._make()
        with pytest.raises(AuthenticationError):
            security.decode_access_token(secret_key="other-secret", token=token)

    def test_wrong_token_type_rejected(self):
        import jwt

        now = int(time.time())
        token = jwt.encode(
            {"sub": "x", "sid": "y", "typ": "refresh", "iat": now, "exp": now + 60},
            "test-secret",
            algorithm="HS256",
        )
        with pytest.raises(AuthenticationError):
            security.decode_access_token(secret_key="test-secret", token=token)

    def test_garbage_token_rejected(self):
        with pytest.raises(AuthenticationError):
            security.decode_access_token(secret_key="test-secret", token="garbage.token.here")


class TestPasswordPolicy:
    def test_too_short_rejected(self):
        with pytest.raises(ValueError):
            validate_password_strength("Ab1!", min_length=10)

    def test_no_digit_rejected(self):
        with pytest.raises(ValueError):
            validate_password_strength("OnlyLettersHere", min_length=10)

    def test_no_letter_rejected(self):
        with pytest.raises(ValueError):
            validate_password_strength("1234567890", min_length=10)

    def test_common_password_rejected(self):
        with pytest.raises(ValueError):
            validate_password_strength("password1234", min_length=10)

    def test_strong_password_accepted(self):
        validate_password_strength("Reasonably-Str0ng", min_length=10)
