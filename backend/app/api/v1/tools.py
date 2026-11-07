"""Tool endpoints: registry listing and direct (user-initiated) execution."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.api.deps import DbDep, PrincipalDep, RegistryDep, SettingsDep, ToolExecutorDep
from app.core.errors import AuthorizationError, RequestValidationError
from app.tools.base import ToolContext

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolInvokeIn(BaseModel):
    input: dict


class ToolInvokeOut(BaseModel):
    tool: str
    output: dict
    duration_ms: int
    tool_execution_id: str


@router.get("", summary="List registered tools")
async def list_tools(principal: PrincipalDep, registry: RegistryDep, db: DbDep):
    import sqlalchemy as sa

    from app.db.models import ToolDefinition

    rows = {
        row.key: row
        for row in (
            await db.execute(sa.select(ToolDefinition))
        ).scalars().all()
    }
    result = []
    for tool in registry.all_tools():
        row = rows.get(tool.key)
        result.append(
            {
                "key": tool.key,
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
                "permissions": tool.permissions,
                "default_timeout_s": tool.default_timeout_s,
                "max_timeout_s": tool.max_timeout_s,
                "requires_approval": tool.requires_approval,
                "allowed_in_chat": tool.allowed_in_chat,
                "input_schema": tool.input_json_schema(),
                "output_schema": tool.output_json_schema(),
                "is_enabled": row.is_enabled if row else True,
            }
        )
    return result


@router.get("/{tool_key}", summary="Describe one tool")
async def get_tool(tool_key: str, principal: PrincipalDep, registry: RegistryDep):
    tool = registry.get(tool_key)
    return tool.describe()


@router.post(
    "/{tool_key}/execute",
    response_model=ToolInvokeOut,
    summary="Execute a tool directly (user-initiated; approval-gated tools are refused)",
)
async def invoke_tool(
    tool_key: str,
    payload: ToolInvokeIn,
    principal: PrincipalDep,
    settings: SettingsDep,
    registry: RegistryDep,
    tool_executor: ToolExecutorDep,
):
    """Direct execution for interactive use (e.g. from the future frontend).

    Security: tools flagged ``requires_approval`` cannot run here — they only
    run inside tasks with an approved step.  Sandbox-bound tools need a
    workspace, which direct invocation does not provide, so they are also
    refused here.
    """
    tool = registry.get(tool_key)
    if tool.requires_approval:
        raise AuthorizationError(
            f"tool '{tool.key}' only runs inside approved task steps"
        )
    if tool.sandboxed:
        raise RequestValidationError(
            f"tool '{tool.key}' requires a task workspace; run it through a task"
        )
    ctx = ToolContext(user_id=principal.user_id, workspace=None, origin="chat")
    outcome = await tool_executor.execute(
        tool_key=tool_key, input_data=payload.input, ctx=ctx
    )
    return ToolInvokeOut(
        tool=tool_key,
        output=outcome.output,
        duration_ms=outcome.duration_ms,
        tool_execution_id=str(outcome.tool_execution_id),
    )
