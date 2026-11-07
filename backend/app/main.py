"""Application factory.

Assembly order matters and is deliberate:

1. structured logging
2. FastAPI app + exception handlers (nothing escapes without a JSON error)
3. middleware (request context -> body limit -> security headers -> CORS)
4. routers under /api/v1, metrics endpoint
5. lifespan: engine, shared services, seeding, optional embedded worker

All heavy objects live on ``app.state`` so tests can build isolated apps
with their own settings/database in a single process.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core import telemetry
from app.api.v1.router import api_router
from app.db.session import Database
from app.events.bus import EventBus
from app.gateway.manager import ModelManager
from app.jobs.queue import JobQueue
from app.storage.backends import get_storage
from app.tools.executor import ToolExecutor
from app.tools.registry import get_registry, register_builtin_tools

logger = structlog.stdlib.get_logger("app.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Backend for a conversational / autonomous agentic AI platform. "
            "Model runtimes are integrated through the gateway abstraction; "
            "the deterministic test adapter is the default model for local "
            "development and is clearly labelled as such."
        ),
        openapi_url="/api/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_build_lifespan(settings),
        # OpenAPI discipline: only what the app actually implements
        openapi_tags=[
            {"name": "auth", "description": "Registration, login, tokens, profile"},
            {"name": "conversations", "description": "Conversations, messages, streaming chat"},
            {"name": "files", "description": "Uploads, downloads, extraction status"},
            {"name": "models", "description": "Model configurations and health"},
            {"name": "tasks", "description": "Autonomous task lifecycle and progress"},
            {"name": "approvals", "description": "Human-in-the-loop approvals"},
            {"name": "tools", "description": "Tool registry and direct execution"},
            {"name": "memory", "description": "Persistent memory engine"},
            {"name": "notifications", "description": "User notifications"},
            {"name": "admin", "description": "Operational endpoints (admin role)"},
            {"name": "health", "description": "Liveness / readiness probes"},
            {"name": "realtime", "description": "WebSocket event stream"},
        ],
    )

    # ---------------- exception handling (never leak internals) ----------
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if getattr(exc, "retry_after_s", None):
            headers["Retry-After"] = str(exc.retry_after_s)
        return JSONResponse(
            status_code=exc.http_status,
            headers=headers,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": _safe_details(exc.details),
                },
                "trace_id": _trace_id(request),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_failed",
                    "message": "request payload failed validation",
                    "details": _json_safe(exc.errors())[:20],
                },
                "trace_id": _trace_id(request),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": f"http_{exc.status_code}", "message": str(exc.detail)},
                "trace_id": _trace_id(request),
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            error_type=type(exc).__name__,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "an internal error occurred; see server logs with the trace id",
                },
                "trace_id": _trace_id(request),
            },
        )

    # ---------------- middleware (outermost last) ------------------------
    app.add_middleware(RequestContextMiddleware, metrics_enabled=settings.metrics_enabled)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=settings.is_production)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,  # token auth via Authorization header, no cookies
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-Id", "Idempotency-Key"],
            max_age=600,
        )

    # ---------------- routes ---------------------------------------------
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": f"{settings.api_v1_prefix}/health/live",
        }

    if settings.metrics_enabled:
        metrics_app = telemetry.metrics_asgi_app()
        if metrics_app is not None:

            @app.get("/metrics", include_in_schema=False)
            async def metrics() -> Any:
                from fastapi.responses import Response

                return Response(
                    content=telemetry.metrics_payload(),
                    media_type="text/plain; version=0.0.4; charset=utf-8",
                )

    return app


def _trace_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _json_safe(value: Any) -> Any:
    """Recursively convert validation payloads into JSON-serialisable data
    (pydantic puts raw ValueError objects into error contexts)."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_details(details: Any) -> Any:
    """Error details are operator-controlled data; scrub obvious secrets."""
    if details is None:
        return None
    if isinstance(details, dict):
        redacted = {
            "password",
            "token",
            "api_key",
            "authorization",
            "secret",
            "refresh_token",
            "access_token",
        }
        return {k: ("****" if str(k).lower() in redacted else v) for k, v in details.items()}
    return details


def _build_lifespan(settings: Settings):
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # --- infrastructure ---------------------------------------------
        database = Database(settings)
        bus = EventBus(settings)
        await bus.start()
        registry = get_registry()
        if len(registry) == 0:
            register_builtin_tools(registry)
        model_manager = ModelManager(database, settings)
        tool_executor = ToolExecutor(database, settings, registry)
        storage = get_storage(settings)
        job_queue = JobQueue(database)

        app.state.settings = settings
        app.state.database = database
        app.state.bus = bus
        app.state.tool_registry = registry
        app.state.model_manager = model_manager
        app.state.tool_executor = tool_executor
        app.state.storage = storage
        app.state.job_queue = job_queue

        # --- idempotent seeding -------------------------------------------
        try:
            async with database.session() as session:
                from app.services.models import ensure_seed_models
                from app.tools.registry import ensure_seeded as seed_tools

                await ensure_seed_models(session, settings)
                await seed_tools(session, registry)
            logger.info("seed_data_ensured")
        except Exception as exc:
            logger.warning(
                "seed_failed",
                error=str(exc),
                hint="run `alembic upgrade head` before starting the API",
            )

        # --- optional bootstrap admin --------------------------------------
        if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
            try:
                await _bootstrap_admin(database, settings)
            except Exception as exc:  # pragma: no cover
                logger.warning("bootstrap_admin_failed", error=str(exc))

        # --- optional embedded worker ----------------------------------------
        embedded_worker_task = None
        if settings.worker_embedded:
            from app.workers.runner import Worker

            worker = Worker(database, settings, bus, worker_id="embedded", concurrency=2)
            embedded_worker_task = asyncio.create_task(
                worker.run(), name="embedded-worker"
            )
            app.state.embedded_worker = worker
            logger.info("embedded_worker_started")

        # --- optional OTel ----------------------------------------------------
        telemetry.configure_otel(settings, app)

        logger.info(
            "application_started",
            environment=settings.environment,
            version=__version__,
        )
        try:
            yield
        finally:
            if embedded_worker_task is not None:
                app.state.embedded_worker.stop()
                embedded_worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await embedded_worker_task
            await bus.close()
            await database.dispose()
            logger.info("application_stopped")

    return lifespan


async def _bootstrap_admin(database: Database, settings: Settings) -> None:
    import sqlalchemy as sa

    from app.core.security import hash_password
    from app.core.utils import new_uuid
    from app.db.enums import UserRole
    from app.db.models import User

    async with database.session() as session:
        existing = await session.scalar(
            sa.select(User).where(User.email == settings.bootstrap_admin_email.lower())
        )
        if existing is not None:
            return
        session.add(
            User(
                id=new_uuid(),
                email=settings.bootstrap_admin_email.lower(),
                password_hash=hash_password(settings.bootstrap_admin_password),
                display_name="Administrator",
                role=UserRole.ADMIN,
            )
        )
        await session.commit()
        logger.info("bootstrap_admin_created", email=settings.bootstrap_admin_email)


# Entry point: uvicorn --factory app.main:create_app
# (no module-level app instance — tests build isolated apps via create_app)

