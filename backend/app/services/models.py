"""Model configuration service: listing, CRUD, seeding."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, RequestValidationError
from app.core.utils import new_uuid, utcnow
from app.db.enums import ModelAdapterType
from app.db.models import ModelConfig
from app.gateway.manager import ModelManager

TEST_MODEL_NAME = "test-echo"
SALUKI_MODEL_NAME = "saluki-27b"


async def list_configs(session: AsyncSession) -> list[ModelConfig]:
    rows = (
        (
            await session.execute(
                sa.select(ModelConfig).order_by(ModelConfig.is_default.desc(), ModelConfig.name)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_config(session: AsyncSession, config_id) -> ModelConfig:
    config = await session.get(ModelConfig, config_id)
    if config is None:
        raise NotFoundError("model configuration not found")
    return config


async def create_config(
    session: AsyncSession,
    manager: ModelManager,
    *,
    payload,
) -> ModelConfig:
    existing = await session.scalar(
        sa.select(ModelConfig).where(ModelConfig.name == payload.name)
    )
    if existing is not None:
        raise ConflictError(f"model '{payload.name}' already exists")
    if payload.adapter_type == ModelAdapterType.OPENAI_COMPATIBLE and not payload.endpoint:
        raise RequestValidationError("openai_compatible adapters require an endpoint")
    if payload.is_default:
        await _clear_default(session)
    config = ModelConfig(
        id=new_uuid(),
        name=payload.name,
        display_name=payload.display_name,
        adapter_type=payload.adapter_type,
        endpoint=payload.endpoint,
        api_key=payload.api_key,
        model_id=payload.model_id,
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        capabilities=payload.capabilities or _default_capabilities(payload.adapter_type),
        parameters=payload.parameters or {},
        is_default=payload.is_default,
        is_enabled=payload.is_enabled,
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)
    manager.invalidate(config.id)
    return config


async def update_config(
    session: AsyncSession,
    manager: ModelManager,
    *,
    config_id,
    payload,
) -> ModelConfig:
    config = await get_config(session, config_id)
    changes = payload.model_dump(exclude_unset=True)
    if "api_key" in changes and changes["api_key"] is None:
        changes.pop("api_key")
    if changes.get("is_default"):
        await _clear_default(session)
    for field, value in changes.items():
        setattr(config, field, value)
    config.updated_at = utcnow()
    await session.commit()
    await session.refresh(config)
    manager.invalidate(config.id)
    return config


async def _clear_default(session: AsyncSession) -> None:
    await session.execute(
        sa.update(ModelConfig).where(ModelConfig.is_default.is_(True)).values(is_default=False)
    )


def _default_capabilities(adapter_type: ModelAdapterType) -> dict:
    if adapter_type == ModelAdapterType.TEST:
        return {"streaming": True, "tool_calls": True, "structured_output": True}
    # For real runtimes start conservative; adjust per verified behaviour.
    return {"streaming": True, "tool_calls": False, "structured_output": False}


async def ensure_seed_models(session: AsyncSession, settings: Settings) -> None:
    """Idempotently seed the built-in model configurations.

    * ``test-echo``   — in-process deterministic test adapter (always seeded).
    * ``saluki-27b``  — OpenAI-compatible boundary for the primary model;
                        enabled only when AGP_SALUKI_ENDPOINT is configured.
    """
    test_model = await session.scalar(
        sa.select(ModelConfig).where(ModelConfig.name == TEST_MODEL_NAME)
    )
    if test_model is None:
        session.add(
            ModelConfig(
                id=new_uuid(),
                name=TEST_MODEL_NAME,
                display_name="Test Echo Adapter (deterministic, not a model)",
                adapter_type=ModelAdapterType.TEST,
                context_window=8192,
                max_output_tokens=2048,
                capabilities={
                    "streaming": True,
                    "tool_calls": True,
                    "structured_output": True,
                },
                parameters={},
                is_default=True,
                is_enabled=True,
            )
        )
        await session.flush()

    saluki = await session.scalar(
        sa.select(ModelConfig).where(ModelConfig.name == SALUKI_MODEL_NAME)
    )
    if saluki is None:
        session.add(
            ModelConfig(
                id=new_uuid(),
                name=SALUKI_MODEL_NAME,
                display_name="Underdog Saluki 27B (integration boundary)",
                adapter_type=ModelAdapterType.OPENAI_COMPATIBLE,
                endpoint=settings.saluki_endpoint,
                api_key=(
                    settings.saluki_api_key.get_secret_value()
                    if settings.saluki_api_key
                    else None
                ),
                model_id=settings.saluki_model_id,
                context_window=settings.saluki_context_window,
                max_output_tokens=4096,
                capabilities={
                    "streaming": True,
                    "tool_calls": False,   # adjust after real-runtime verification
                    "structured_output": False,
                },
                parameters={},
                is_default=False,
                is_enabled=settings.saluki_endpoint is not None,
            )
        )
        await session.flush()
    else:
        # keep endpoint in sync with environment (credential rotation)
        saluki.endpoint = settings.saluki_endpoint or saluki.endpoint
        if settings.saluki_api_key is not None:
            saluki.api_key = settings.saluki_api_key.get_secret_value()

    await session.commit()
