"""API v1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    approvals,
    auth,
    conversations,
    files,
    health,
    memory,
    models,
    notifications,
    tasks,
    tools,
    ws,
)

api_router = APIRouter()

for module_router in (
    auth.router,
    conversations.router,
    files.router,
    models.router,
    tasks.router,
    approvals.router,
    tools.router,
    memory.router,
    notifications.router,
    admin.router,
    health.router,
    ws.router,
):
    api_router.include_router(module_router)
