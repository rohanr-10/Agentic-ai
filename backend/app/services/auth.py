"""Authentication service.

Implements registration, login, refresh-token rotation with reuse
detection, logout, password reset and profile updates.  Security rules:

* Argon2id password hashing, never plaintext, never logged.
* Refresh tokens: 256-bit random, only SHA-256 digests stored, rotated on
  every refresh.  Presenting a revoked token revokes ALL of the user's
  sessions (token-reuse/theft response).
* Login failures never reveal whether the account exists.
* Every security-relevant event lands in the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError, RequestValidationError
from app.core.utils import new_token, new_uuid, utcnow
from app.db.enums import AuditSeverity, UserRole
from app.db.models import AuthSession, PasswordResetToken, User
from app.services import audit
from app.services.audit import (
    AUTH_LOGIN,
    AUTH_LOGIN_FAILED,
    AUTH_PASSWORD_CHANGED,
    AUTH_PASSWORD_RESET_COMPLETED,
    AUTH_PASSWORD_RESET_REQUESTED,
    AUTH_REFRESH,
    AUTH_REFRESH_REUSE,
    AUTH_REGISTER,
    AUTH_SESSIONS_REVOKED,
)

logger = structlog.stdlib.get_logger("app.auth")

RESET_TOKEN_TTL_S = 60 * 60


@dataclass
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


@dataclass
class IssuedTokens:
    session: AuthSession
    access_token: str
    refresh_token: str
    expires_in: int


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- registration -----------------------------------------------------
    async def register(
        self, *, email: str, password: str, display_name: str, meta: RequestMeta
    ) -> tuple[User, IssuedTokens]:
        email = email.strip().lower()
        existing = await self.session.scalar(sa.select(User).where(User.email == email))
        if existing is not None:
            raise ConflictError("an account with this email already exists")
        password_hash = security.hash_password(password)
        user = User(
            id=new_uuid(),
            email=email,
            password_hash=password_hash,
            display_name=display_name.strip(),
            role=UserRole.USER,
        )
        self.session.add(user)
        await self.session.flush()
        await audit.record(
            self.session,
            event_type=AUTH_REGISTER,
            user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip=meta.ip,
            user_agent=meta.user_agent,
            request_id=meta.request_id,
        )
        issued = await self._issue_session(user, meta=meta)
        await self.session.commit()
        return user, issued

    # -- login --------------------------------------------------------------
    async def login(
        self, *, email: str, password: str, meta: RequestMeta
    ) -> tuple[User, IssuedTokens]:
        email = email.strip().lower()
        user = await self.session.scalar(sa.select(User).where(User.email == email))
        if user is None or not security.verify_password(user.password_hash, password):
            await audit.record(
                self.session,
                event_type=AUTH_LOGIN_FAILED,
                user_id=user.id if user else None,
                severity=AuditSeverity.WARNING,
                ip=meta.ip,
                user_agent=meta.user_agent,
                request_id=meta.request_id,
                details={"email_domain": email.split("@")[-1] if "@" in email else ""},
            )
            await self.session.commit()
            raise AuthenticationError("invalid email or password")
        if not user.is_active:
            await audit.record(
                self.session,
                event_type=AUTH_LOGIN_FAILED,
                user_id=user.id,
                severity=AuditSeverity.WARNING,
                ip=meta.ip,
                request_id=meta.request_id,
                details={"reason": "account_disabled"},
            )
            await self.session.commit()
            raise AuthenticationError("invalid email or password")

        # transparent Argon2 parameter upgrades
        if security.password_needs_rehash(user.password_hash):
            user.password_hash = security.hash_password(password)

        user.last_login_at = utcnow()
        await audit.record(
            self.session,
            event_type=AUTH_LOGIN,
            user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip=meta.ip,
            user_agent=meta.user_agent,
            request_id=meta.request_id,
        )
        issued = await self._issue_session(user, meta=meta)
        await self.session.commit()
        return user, issued

    # -- refresh rotation -----------------------------------------------------
    async def refresh(
        self, *, refresh_token: str, meta: RequestMeta
    ) -> tuple[User, IssuedTokens]:
        token_hash = security.hash_token(refresh_token)
        auth_session = await self.session.scalar(
            sa.select(AuthSession).where(AuthSession.refresh_token_hash == token_hash)
        )
        if auth_session is None:
            raise AuthenticationError("invalid refresh token")
        if auth_session.revoked_at is not None:
            # Reuse of a rotated token => assume theft, revoke everything.
            await self._revoke_all_for_user(auth_session.user_id, reason="refresh_token_reuse")
            await audit.record(
                self.session,
                event_type=AUTH_REFRESH_REUSE,
                user_id=auth_session.user_id,
                severity=AuditSeverity.CRITICAL,
                resource_type="auth_session",
                resource_id=auth_session.id,
                ip=meta.ip,
                request_id=meta.request_id,
            )
            await self.session.commit()
            raise AuthenticationError("refresh token reuse detected; all sessions revoked")
        if auth_session.expires_at <= utcnow():
            raise AuthenticationError("refresh token expired")

        user = await self.session.get(User, auth_session.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("invalid refresh token")

        auth_session.revoked_at = utcnow()
        auth_session.last_used_at = utcnow()
        await audit.record(
            self.session,
            event_type=AUTH_REFRESH,
            user_id=user.id,
            resource_type="auth_session",
            resource_id=auth_session.id,
            ip=meta.ip,
            request_id=meta.request_id,
        )
        issued = await self._issue_session(
            user, meta=meta, rotated_from=auth_session.id
        )
        await self.session.commit()
        return user, issued

    # -- logout ---------------------------------------------------------------
    async def logout(self, *, session_id: UUID, user_id: UUID, meta: RequestMeta) -> None:
        auth_session = await self.session.get(AuthSession, session_id)
        if auth_session is not None and auth_session.user_id == user_id:
            auth_session.revoked_at = utcnow()
        await audit.record(
            self.session,
            event_type="auth.logout",
            user_id=user_id,
            resource_type="auth_session",
            resource_id=session_id,
            ip=meta.ip,
            request_id=meta.request_id,
        )
        await self.session.commit()

    # -- password reset ----------------------------------------------------------
    async def request_password_reset(self, *, email: str, meta: RequestMeta) -> None:
        """Always behaves identically whether or not the account exists."""
        email = email.strip().lower()
        user = await self.session.scalar(sa.select(User).where(User.email == email))
        if user is not None:
            token = new_token()
            self.session.add(
                PasswordResetToken(
                    id=new_uuid(),
                    user_id=user.id,
                    token_hash=security.hash_token(token),
                    expires_at=utcnow() + timedelta(seconds=RESET_TOKEN_TTL_S),
                )
            )
            await audit.record(
                self.session,
                event_type=AUTH_PASSWORD_RESET_REQUESTED,
                user_id=user.id,
                ip=meta.ip,
                request_id=meta.request_id,
            )
            await self.session.commit()
            # Delivery integration point: plug in an email provider here.
            logger.info(
                "password_reset_token_issued",
                hint="configure an email delivery provider in production",
                user_id=str(user.id),
            )
        else:
            await self.session.commit()

    async def confirm_password_reset(
        self, *, token: str, new_password: str, meta: RequestMeta
    ) -> None:
        token_hash = security.hash_token(token)
        reset = await self.session.scalar(
            sa.select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        if (
            reset is None
            or reset.used_at is not None
            or reset.expires_at <= utcnow()
        ):
            raise AuthenticationError("invalid or expired reset token")
        user = await self.session.get(User, reset.user_id)
        if user is None:
            raise AuthenticationError("invalid or expired reset token")
        user.password_hash = security.hash_password(new_password)
        reset.used_at = utcnow()
        await self._revoke_all_for_user(user.id, reason="password_reset")
        await audit.record(
            self.session,
            event_type=AUTH_PASSWORD_RESET_COMPLETED,
            user_id=user.id,
            severity=AuditSeverity.WARNING,
            ip=meta.ip,
            request_id=meta.request_id,
        )
        await self.session.commit()

    async def change_password(
        self,
        *,
        user: User,
        current_password: str,
        new_password: str,
        keep_session_id: UUID | None,
        meta: RequestMeta,
    ) -> None:
        if not security.verify_password(user.password_hash, current_password):
            raise AuthenticationError("current password is incorrect")
        user.password_hash = security.hash_password(new_password)
        # revoke every session except the one making the request
        sessions = (
            (
                await self.session.execute(
                    sa.select(AuthSession).where(
                        AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        for auth_session in sessions:
            if keep_session_id is None or auth_session.id != keep_session_id:
                auth_session.revoked_at = utcnow()
        await audit.record(
            self.session,
            event_type=AUTH_PASSWORD_CHANGED,
            user_id=user.id,
            severity=AuditSeverity.WARNING,
            ip=meta.ip,
            request_id=meta.request_id,
        )
        await self.session.commit()

    # -- profile ---------------------------------------------------------------
    async def update_profile(
        self, *, user: User, display_name: str, meta: RequestMeta
    ) -> User:
        user.display_name = display_name.strip()
        await audit.record(
            self.session,
            event_type="user.updated",
            user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip=meta.ip,
            request_id=meta.request_id,
        )
        await self.session.commit()
        await self.session.refresh(user)
        return user

    # -- helpers ---------------------------------------------------------------
    async def _issue_session(
        self,
        user: User,
        *,
        meta: RequestMeta,
        rotated_from: UUID | None = None,
    ) -> IssuedTokens:
        refresh_token = new_token()
        auth_session = AuthSession(
            id=new_uuid(),
            user_id=user.id,
            refresh_token_hash=security.hash_token(refresh_token),
            expires_at=utcnow() + timedelta(seconds=self.settings.refresh_token_ttl_seconds),
            rotated_from_id=rotated_from,
            user_agent=(meta.user_agent or "")[:300] or None,
            ip=meta.ip,
        )
        self.session.add(auth_session)
        await self.session.flush()
        access_token = security.create_access_token(
            secret_key=self.settings.secret_key.get_secret_value(),
            user_id=user.id,
            session_id=auth_session.id,
            role=user.role.value,
            ttl_seconds=self.settings.access_token_ttl_seconds,
        )
        return IssuedTokens(
            session=auth_session,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self.settings.access_token_ttl_seconds,
        )

    async def _revoke_all_for_user(self, user_id: UUID, *, reason: str) -> int:
        result = await self.session.execute(
            sa.update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        count = result.rowcount or 0
        if count:
            await audit.record(
                self.session,
                event_type=AUTH_SESSIONS_REVOKED,
                user_id=user_id,
                severity=AuditSeverity.WARNING,
                details={"reason": reason, "count": count},
            )
        return count

    async def get_session_for_access(
        self, session_id: UUID
    ) -> tuple[User, AuthSession] | None:
        """Validate that a JWT's session is still live (called per request)."""
        auth_session = await self.session.get(AuthSession, session_id)
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= utcnow()
        ):
            return None
        user = await self.session.get(User, auth_session.user_id)
        if user is None or not user.is_active:
            return None
        return user, auth_session


def validate_registration_password(password: str, settings: Settings) -> None:
    """Password policy shared by register/reset/change endpoints."""
    from app.schemas.auth import validate_password_strength

    try:
        validate_password_strength(password, min_length=settings.password_min_length)
    except ValueError as exc:
        raise RequestValidationError(str(exc)) from exc


def _never(value: Any) -> Any:  # pragma: no cover - defensive
    raise AssertionError("secrets must never be serialized")
