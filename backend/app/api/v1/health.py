"""Health endpoints: liveness, readiness."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["health"])


class ComponentStatus(BaseModel):
    ok: bool
    detail: str


class ReadinessReport(BaseModel):
    status: str
    checked_at: str
    components: dict[str, ComponentStatus]


class LivenessReport(BaseModel):
    status: str
    time: str


@router.get("/live", response_model=LivenessReport, summary="Liveness probe (process is up)")
async def live():
    return LivenessReport(status="alive", time=_now())


@router.get(
    "/ready",
    response_model=ReadinessReport,
    summary="Readiness probe (dependencies usable)",
    responses={503: {"model": ReadinessReport}},
)
async def ready(request: Request):
    app = request.app
    settings = app.state.settings
    components: dict[str, ComponentStatus] = {}

    # database
    db_ok = await app.state.database.ping()
    migrations_ok = await _migrations_current(app)
    components["database"] = ComponentStatus(
        ok=db_ok and migrations_ok,
        detail=(
            "reachable"
            if db_ok
            else "unreachable"
        ) + (", migrations current" if migrations_ok else ", migrations NOT current"),
    )

    # storage
    try:
        await app.state.storage.probe()
        components["storage"] = ComponentStatus(ok=True, detail="writable")
    except Exception as exc:
        components["storage"] = ComponentStatus(ok=False, detail=f"probe failed: {type(exc).__name__}")

    # redis (optional component)
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis  # type: ignore

            client = aioredis.from_url(settings.redis_url, socket_timeout=2)
            await client.ping()
            await client.aclose()
            components["redis"] = ComponentStatus(ok=True, detail="reachable")
        except Exception as exc:
            components["redis"] = ComponentStatus(ok=False, detail=f"unreachable: {type(exc).__name__}")

    # default model runtime
    try:
        import sqlalchemy as sa

        from app.db.models import ModelConfig

        async with app.state.database.session() as session:
            default_model = await session.scalar(
                sa.select(ModelConfig).where(ModelConfig.is_default.is_(True))
            )
            model_id = default_model.id if default_model else None
        if model_id is None:
            components["model"] = ComponentStatus(ok=True, detail="no default model configured")
        else:
            async with app.state.database.session() as session:
                _, report = await app.state.model_manager.health(session, model_id)
            components["model"] = ComponentStatus(ok=report.healthy, detail=report.detail)
    except Exception as exc:  # pragma: no cover
        components["model"] = ComponentStatus(ok=False, detail=f"check failed: {type(exc).__name__}")

    ready_status = all(component.ok for component in components.values())
    import json

    from fastapi.responses import JSONResponse

    payload = ReadinessReport(
        status="ready" if ready_status else "unavailable",
        checked_at=_now(),
        components=components,
    )
    return JSONResponse(
        status_code=200 if ready_status else 503,
        content=json.loads(payload.model_dump_json()),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _migrations_current(app) -> bool:
    """Compare the database's alembic revision with the newest local revision."""
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        alembic_cfg = AlembicConfig("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        head = script.get_current_head()

        import sqlalchemy as sa

        async with app.state.database.session() as session:
            result = await session.execute(
                sa.text("SELECT version_num FROM alembic_version")
            )
            row = result.first()
        return row is not None and row[0] == head
    except Exception as exc:
        import structlog

        structlog.stdlib.get_logger("app.health").warning(
            "migration_currency_check_failed", error=f"{type(exc).__name__}: {exc}"
        )
        return False
