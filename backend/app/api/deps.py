"""FastAPI dependency wiring (DI boundary between API and services)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPBearer
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthenticationError, AuthorizationError
from app.core.ratelimit import client_identity, enforce_rate_limit, get_rate_limiter
from app.core.security import decode_access_token
from app.db.enums import UserRole
from app.db.models import AuthSession, User
from app.db.session import Database
from app.events.bus import EventBus
from app.jobs.queue import JobQueue
from app.services.auth import RequestMeta


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped session; services own commit/rollback explicitly."""
    database: Database = request.app.state.database
    async with database.session() as session:
        yield session


async def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.bus


def get_job_queue(request: Request) -> JobQueue:
    return request.app.state.job_queue


def get_model_manager(request: Request):
    return request.app.state.model_manager


def get_tool_registry(request: Request):
    return request.app.state.tool_registry


def get_tool_executor(request: Request):
    return request.app.state.tool_executor


def get_storage(request: Request):
    return request.app.state.storage


DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
BusDep = Annotated[EventBus, Depends(get_event_bus)]
ManagerDep = Annotated[Any, Depends(get_model_manager)]
RegistryDep = Annotated[Any, Depends(get_tool_registry)]
ToolExecutorDep = Annotated[Any, Depends(get_tool_executor)]
StorageDep = Annotated[Any, Depends(get_storage)]
QueueDep = Annotated[JobQueue, Depends(get_job_queue)]


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip=client_identity(request, request.app.state.settings.trust_proxy_headers),
        user_agent=request.headers.get("user-agent"),
        request_id=request.headers.get("x-request-id"),
    )


def bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
    scheme, param = get_authorization_scheme_param(authorization)
    if scheme.lower() != "bearer" or not param:
        return None
    return param


@dataclass
class Principal:
    user: User
    session: AuthSession

    @property
    def user_id(self):
        return self.user.id

    def require_role(self, role: UserRole) -> None:
        if self.user.role != role:
            raise AuthorizationError("this operation requires elevated privileges")


_bearer_scheme = HTTPBearer(auto_error=False, description="Access token from /auth/login or /auth/register")


async def get_principal(
    request: Request, db: DbDep, credentials=Depends(_bearer_scheme)
) -> Principal:
    """Authenticate via Bearer access token and re-validate the session.

    Sessions are checked against the database on every request, so logout /
    rotation / global revocation take effect immediately.
    """
    from app.services.auth import AuthService

    token = credentials.credentials if credentials else bearer_token(request)
    if token is None:
        raise AuthenticationError("missing bearer access token")
    settings: Settings = request.app.state.settings
    claims = decode_access_token(
        secret_key=settings.secret_key.get_secret_value(), token=token
    )
    auth_service = AuthService(db, settings)
    import uuid as _uuid

    result = await auth_service.get_session_for_access(_uuid.UUID(claims["sid"]))
    if result is None:
        raise AuthenticationError("session is no longer valid")
    user, auth_session = result
    from app.core.middleware import bind_user_to_logs

    bind_user_to_logs(user.id)
    return Principal(user=user, session=auth_session)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_admin_principal(principal: PrincipalDep) -> Principal:
    principal.require_role(UserRole.ADMIN)
    return principal


AdminDep = Annotated[Principal, Depends(get_admin_principal)]


async def principal_from_token(app, token: str) -> Principal:
    """Auth for transports that cannot set headers (SSE/WS query tokens)."""
    import uuid as _uuid

    from app.services.auth import AuthService

    settings: Settings = app.state.settings
    claims = decode_access_token(secret_key=settings.secret_key.get_secret_value(), token=token)
    async with app.state.database.session() as session:
        auth_service = AuthService(session, settings)
        result = await auth_service.get_session_for_access(_uuid.UUID(claims["sid"]))
        if result is None:
            raise AuthenticationError("session is no longer valid")
        user, auth_session = result
        return Principal(user=user, session=auth_session)


def rate_limit(scope: str, *, limit: int | None = None, window_s: float = 60.0):
    """Dependency factory: ``dependencies=[Depends(rate_limit("auth-login"))]``.

    Scopes map to their configured limits:

    * ``auth-*``     -> ``rate_limit_auth_per_minute`` (strict)
    * ``file-upload``-> ``rate_limit_upload_per_minute``
    * anything else  -> ``rate_limit_default_per_minute``
    """

    async def _dep(request: Request) -> None:
        settings: Settings = request.app.state.settings
        if not settings.rate_limit_enabled:
            return
        auth_scopes = {
            "auth-login",
            "auth-register",
            "auth-refresh",
            "auth-reset",
        }
        if limit is not None:
            effective = limit
        elif scope in auth_scopes:
            effective = settings.rate_limit_auth_per_minute
        elif scope == "file-upload":
            effective = settings.rate_limit_upload_per_minute
        else:
            effective = settings.rate_limit_default_per_minute
        limiter = get_rate_limiter(settings)

        identity = "anonymous"
        token = bearer_token(request)
        if token:
            try:
                claims = decode_access_token(
                    secret_key=settings.secret_key.get_secret_value(), token=token
                )
                identity = claims["sub"]
            except AuthenticationError:
                pass
        if identity == "anonymous":
            identity = client_identity(request, settings.trust_proxy_headers)

        await enforce_rate_limit(
            limiter,
            scope=scope,
            identity=identity,
            limit=effective,
            window_s=window_s,
        )

    return _dep
