"""Password hashing (Argon2id) and JWT access tokens.

* Passwords: Argon2id via ``argon2-cffi`` with parameters recorded in the
  hash string; rehash-on-login upgrades parameters if policy changes.
* Access tokens: short-lived signed JWTs (HS256) carrying the user id,
  session id and role snapshot.  Every request re-validates the session
  against the database, so revoking a session immediately invalidates its
  access tokens (see ``app.api.deps.get_current_user``).
* Refresh / reset tokens: opaque random strings; only their SHA-256 digest
  is persisted.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.errors import AuthenticationError
from app.core.utils import sha256_hex

_password_hasher = PasswordHasher()  # Argon2id, library-default strong params


def hash_password(plain: str) -> str:
    return _password_hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    try:
        return _password_hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def hash_token(token: str) -> str:
    return sha256_hex(token)


def create_access_token(
    *,
    secret_key: str,
    user_id: UUID,
    session_id: UUID,
    role: str,
    ttl_seconds: int,
    issuer: str = "agentic-platform",
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "role": role,
        "typ": "access",
        "iat": now,
        "nbf": now,
        "exp": now + ttl_seconds,
        "iss": issuer,
    }
    return jwt.encode(claims, secret_key, algorithm="HS256")


def decode_access_token(*, secret_key: str, token: str) -> dict[str, Any]:
    """Decode + validate an access token.  Raises ``AuthenticationError``."""
    try:
        claims = jwt.decode(
            token,
            secret_key,
            algorithms=["HS256"],
            issuer="agentic-platform",
            options={"require": ["exp", "iat", "sub", "sid", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("access token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("invalid access token") from exc
    if claims.get("typ") != "access":
        raise AuthenticationError("invalid token type")
    return claims
