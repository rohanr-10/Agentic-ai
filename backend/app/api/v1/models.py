"""Model configuration endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import AdminDep, DbDep, ManagerDep, PrincipalDep
from app.schemas.models import (
    ModelConfigCreateIn,
    ModelConfigOut,
    ModelConfigUpdateIn,
    ModelHealthOut,
)
from app.services import models as model_service

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelConfigOut], summary="List model configurations")
async def list_models(principal: PrincipalDep, db: DbDep):
    rows = await model_service.list_configs(db)
    return [ModelConfigOut.from_config(row) for row in rows]


@router.get("/{model_id}", response_model=ModelConfigOut, summary="Get a model configuration")
async def get_model(model_id: UUID, principal: PrincipalDep, db: DbDep):
    row = await model_service.get_config(db, model_id)
    return ModelConfigOut.from_config(row)


@router.get("/{model_id}/health", response_model=ModelHealthOut, summary="Probe a model runtime")
async def model_health(
    model_id: UUID, principal: PrincipalDep, db: DbDep, manager: ManagerDep
):
    config, report = await manager.health(db, model_id)
    return ModelHealthOut(
        model_id=config.id,
        name=config.name,
        healthy=report.healthy,
        detail=report.detail,
        checked_at=report.checked_at,
    )


@router.post(
    "",
    response_model=ModelConfigOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a model configuration (admin)",
)
async def create_model(payload: ModelConfigCreateIn, admin: AdminDep, db: DbDep, manager: ManagerDep):
    row = await model_service.create_config(db, manager, payload=payload)
    return ModelConfigOut.from_config(row)


@router.patch("/{model_id}", response_model=ModelConfigOut, summary="Update a model configuration (admin)")
async def update_model(
    model_id: UUID,
    payload: ModelConfigUpdateIn,
    admin: AdminDep,
    db: DbDep,
    manager: ManagerDep,
):
    row = await model_service.update_config(db, manager, config_id=model_id, payload=payload)
    return ModelConfigOut.from_config(row)
