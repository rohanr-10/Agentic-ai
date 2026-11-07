"""Gateway manager: adapter resolution, timeouts, telemetry, health caching."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import telemetry
from app.core.config import Settings
from app.core.errors import ModelTimeoutError, NotFoundError
from app.core.utils import utcnow
from app.db.models import ModelConfig
from app.db.session import Database
from app.gateway import base as gateway_base
from app.gateway.base import (
    HealthReport,
    InferenceRequest,
    InferenceResponse,
    ModelAdapter,
    StreamEvent,
    adapter_class_for,
)

# Imported for side-effect: registers built-in adapters.
from app.gateway.adapters import test_adapter as _test_adapter_module  # noqa: F401
from app.gateway.adapters import openai_compatible as _openai_module  # noqa: F401

logger = structlog.stdlib.get_logger("app.gateway")


class ModelManager:
    """Resolves model configs into adapter instances and enforces contracts.

    Adapters are cached per config row and invalidated when the row's
    ``updated_at`` changes, so config edits take effect without restart.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self._adapter_cache: dict[str, tuple[str, ModelAdapter]] = {}
        self._health_cache: dict[str, tuple[float, HealthReport]] = {}

    # -- resolution -----------------------------------------------------------
    async def resolve(
        self, session: AsyncSession, model_config_id: UUID | None = None
    ) -> ModelAdapter:
        config = await self._load_config(session, model_config_id)
        return self._adapter_for(config)

    async def _load_config(
        self, session: AsyncSession, model_config_id: UUID | None
    ) -> ModelConfig:
        if model_config_id is not None:
            config = await session.get(ModelConfig, model_config_id)
            if config is None:
                raise NotFoundError("model configuration not found")
        else:
            config = await session.scalar(
                sa.select(ModelConfig).where(
                    ModelConfig.is_default.is_(True), ModelConfig.is_enabled.is_(True)
                )
            )
            if config is None:
                config = await session.scalar(
                    sa.select(ModelConfig).where(
                        ModelConfig.name == self.settings.default_model_name
                    )
                )
            if config is None:
                raise NotFoundError(
                    "no default model configured",
                    details={"hint": "seed models via app startup or POST /models"},
                )
        if not config.is_enabled:
            raise NotFoundError(f"model '{config.name}' is disabled")
        return config

    def _adapter_for(self, config: ModelConfig) -> ModelAdapter:
        cache_key = str(config.id)
        stamp = config.updated_at.isoformat() if config.updated_at else ""
        cached = self._adapter_cache.get(cache_key)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        adapter_cls = adapter_class_for(config.adapter_type.value)
        adapter = adapter_cls(config)
        self._adapter_cache[cache_key] = (stamp, adapter)
        return adapter

    def invalidate(self, config_id: UUID | str) -> None:
        self._adapter_cache.pop(str(config_id), None)
        self._health_cache.pop(str(config_id), None)

    # -- inference --------------------------------------------------------------
    async def generate(
        self,
        session: AsyncSession,
        request: InferenceRequest,
        *,
        model_config_id: UUID | None = None,
        timeout_s: float | None = None,
    ) -> InferenceResponse:
        adapter = await self.resolve(session, model_config_id)
        timeout = timeout_s or self.settings.model_request_timeout_s
        started = time.perf_counter()
        status = "ok"
        try:
            response = await _wait_with_timeout(
                adapter.generate(request), timeout_s=timeout
            )
            return response
        except ModelTimeoutError:
            status = "timeout"
            raise
        except Exception:
            status = "error"
            raise
        finally:
            telemetry.record_model_request(
                model=adapter.name,
                adapter=type(adapter).__name__,
                status=status,
                duration_s=time.perf_counter() - started,
            )

    async def stream(
        self,
        session: AsyncSession,
        request: InferenceRequest,
        *,
        model_config_id: UUID | None = None,
    ):
        """Async-generator wrapper adding telemetry + error normalisation."""
        adapter = await self.resolve(session, model_config_id)
        started = time.perf_counter()
        status = "ok"
        try:
            async for event in adapter.stream(request):
                if event.kind == "error":
                    status = "error"
                yield event
        except Exception:
            status = "error"
            raise
        finally:
            telemetry.record_model_request(
                model=adapter.name,
                adapter=type(adapter).__name__,
                status=status,
                duration_s=time.perf_counter() - started,
            )

    # -- health ------------------------------------------------------------------
    async def health(
        self,
        session: AsyncSession,
        model_config_id: UUID | None = None,
        *,
        force: bool = False,
    ) -> tuple[ModelConfig, HealthReport]:
        config = await self._load_config(session, model_config_id)
        cache_key = str(config.id)
        cached = self._health_cache.get(cache_key)
        now = time.monotonic()
        if (
            not force
            and cached is not None
            and now - cached[0] < self.settings.model_health_cache_s
        ):
            return config, cached[1]
        adapter = self._adapter_for(config)
        report = await adapter.health()
        self._health_cache[cache_key] = (now, report)
        return config, report

    async def describe(self, session: AsyncSession, model_config_id: UUID) -> dict[str, Any]:
        config = await self._load_config(session, model_config_id)
        adapter = self._adapter_for(config)
        return {
            "name": config.name,
            "adapter": config.adapter_type.value,
            "capabilities": {
                "streaming": adapter.capabilities.streaming,
                "tool_calls": adapter.capabilities.tool_calls,
                "structured_output": adapter.capabilities.structured_output,
                "context_window": adapter.capabilities.context_window,
            },
        }


async def _wait_with_timeout(coro: Any, *, timeout_s: float) -> InferenceResponse:
    import asyncio

    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise ModelTimeoutError(
            f"model inference exceeded {timeout_s}s"
        ) from exc


def default_capability_flags() -> dict[str, bool]:
    return {"streaming": True, "tool_calls": True, "structured_output": True}
