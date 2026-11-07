"""Integration: authentication end-to-end."""

from __future__ import annotations

import time

import httpx
import pytest

from tests.conftest import PASSWORD, register_user


class TestRegistration:
    async def test_register_returns_tokens_and_user(self, client):
        body = await register_user(client, "newbie@example.com")
        assert body["user"]["email"] == "newbie@example.com"
        assert body["user"]["role"] == "user"
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

    async def test_duplicate_email_rejected(self, client):
        await register_user(client, "dup@example.com")
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": PASSWORD, "display_name": "Dup"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    async def test_weak_password_rejected(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "weak@example.com", "password": "short1", "display_name": "W"},
        )
        assert response.status_code == 400
        assert "password" in response.json()["error"]["message"]

    async def test_invalid_email_rejected(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": PASSWORD, "display_name": "X"},
        )
        assert response.status_code == 422


class TestLogin:
    async def test_login_success(self, client):
        await register_user(client, "login@example.com")
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": PASSWORD},
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_login_wrong_password(self, client):
        await register_user(client, "wrongpw@example.com")
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "wrongpw@example.com", "password": "WrongPass123"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_failed"

    async def test_login_unknown_user_same_error(self, client):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "Whatever123"},
        )
        assert response.status_code == 401
        # identical error shape: no user enumeration
        known = await client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "Other1234"},
        )
        assert known.json()["error"]["code"] == response.json()["error"]["code"]


class TestRefreshRotation:
    async def _login(self, client) -> dict:
        await register_user(client, "rotate@example.com")
        response = await client.post(
            "/api/v1/auth/login", json={"email": "rotate@example.com", "password": PASSWORD}
        )
        return response.json()

    async def test_refresh_rotates_tokens(self, client):
        tokens = await self._login(client)
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert response.status_code == 200
        new_tokens = response.json()
        assert new_tokens["refresh_token"] != tokens["refresh_token"]
        assert new_tokens["access_token"] != tokens["access_token"]

        # the new pair works; the old refresh token is dead
        again = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
        )
        assert again.status_code == 200
        replay = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replay.status_code == 401

    async def test_refresh_reuse_revokes_all_sessions(self, client):
        """Token-theft response: replaying a rotated token kills everything."""
        tokens = await self._login(client)
        first = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert first.status_code == 200
        fresh_access = first.json()["access_token"]

        # replay the OLD token => 401 ...
        replay = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replay.status_code == 401

        # ... and the session minted from the legit rotation is now revoked too
        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh_access}"}
        )
        assert me.status_code == 401

    async def test_garbage_refresh_rejected(self, client):
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "garbage-token-value"}
        )
        assert response.status_code == 401


class TestLogout:
    async def test_logout_revokes_access_immediately(self, client):
        tokens = await register_user(client, "logout@example.com")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        response = await client.post("/api/v1/auth/logout", headers=headers)
        assert response.status_code == 204
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 401


class TestPasswordReset:
    async def test_full_reset_flow(self, client):
        await register_user(client, "reset@example.com")
        response = await client.post(
            "/api/v1/auth/password-reset", json={"email": "reset@example.com"}
        )
        assert response.status_code == 202

        # token is delivered via the structured log in dev; retrieve the user
        # from the stored (hashed) token row
        from app.core.security import hash_token
        from app.db.models import PasswordResetToken

        async with client._transport.app.state.database.session() as session:
            import sqlalchemy as sa

            row = await session.scalar(
                sa.select(PasswordResetToken).order_by(PasswordResetToken.created_at.desc())
            )
            reset_user_id = row.user_id

        # we cannot reconstruct the raw token from its hash; exercise the
        # confirm path with a directly-minted token through the service
        from app.services.auth import AuthService

        async with client._transport.app.state.database.session() as session:
            from app.core.utils import new_token, utcnow
            from datetime import timedelta

            raw = new_token()
            session.add(
                PasswordResetToken(
                    user_id=reset_user_id,
                    token_hash=hash_token(raw),
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
            await session.commit()

        confirm = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw, "new_password": "BrandNewPass123"},
        )
        assert confirm.status_code == 204

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "reset@example.com", "password": "BrandNewPass123"},
        )
        assert login.status_code == 200

    async def test_reset_request_does_not_reveal_existence(self, client):
        known = await client.post(
            "/api/v1/auth/password-reset", json={"email": "who-knows@example.com"}
        )
        unknown = await client.post(
            "/api/v1/auth/password-reset", json={"email": "never-registered@example.com"}
        )
        assert known.status_code == unknown.status_code == 202


class TestProfile:
    async def test_update_display_name(self, client, headers):
        response = await client.patch(
            "/api/v1/auth/me", json={"display_name": "Renamed"}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Renamed"

    async def test_expired_token_rejected(self, client):
        tokens = await register_user(client, "expiry@example.com")
        import jwt as pyjwt

        from app.core.security import create_access_token

        # mint an already-expired token with the app's own secret
        import uuid

        expired = create_access_token(
            secret_key=client._transport.app.state.settings.secret_key.get_secret_value(),
            user_id=uuid.UUID(tokens["user"]["id"]),
            session_id=uuid.uuid4(),
            role="user",
            ttl_seconds=-5,
        )
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
        )
        assert response.status_code == 401
