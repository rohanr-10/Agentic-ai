"""Approval endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import BusDep, DbDep, PrincipalDep
from app.db.enums import ApprovalStatus
from app.schemas.common import Page
from app.schemas.tasks import ApprovalDecisionIn, ApprovalOut
from app.services import approvals as approval_service

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=Page[ApprovalOut], summary="List approval requests")
async def list_approvals(
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: ApprovalStatus | None = Query(default=None, alias="status"),
):
    rows, total = await approval_service.list_approvals(
        db,
        user_id=principal.user_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[ApprovalOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{approval_id}", response_model=ApprovalOut, summary="Get an approval request")
async def get_approval(approval_id: UUID, principal: PrincipalDep, db: DbDep):
    row = await approval_service.get_approval(
        db, user_id=principal.user_id, approval_id=approval_id
    )
    return ApprovalOut.model_validate(row)


@router.post(
    "/{approval_id}",
    response_model=ApprovalOut,
    summary="Approve or reject (re-drives the task through the orchestrator)",
)
async def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionIn,
    principal: PrincipalDep,
    db: DbDep,
    bus: BusDep,
):
    row = await approval_service.decide_approval(
        db,
        bus,
        user_id=principal.user_id,
        approval_id=approval_id,
        decision=payload.decision,
        reason=payload.reason,
    )
    return ApprovalOut.model_validate(row)
