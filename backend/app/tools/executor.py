"""Tool executor: validation, permission/approval enforcement, audit, metrics."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from pydantic import ValidationError

from app.core import telemetry
from app.core.config import Settings
from app.core.errors import (
    AuthorizationError,
    NotFoundError,
    RequestValidationError,
    ToolExecutionError,
)
from app.core.utils import new_uuid, utcnow
from app.db.enums import ToolRunStatus
from app.db.models import ToolDefinition, ToolExecution
from app.db.session import Database
from app.tools.base import Tool, ToolContext


def _json_safe_errors(errors):
    import json
    return json.loads(json.dumps(errors, default=str))
from app.tools.registry import ToolRegistry

logger = structlog.stdlib.get_logger("app.tools.executor")

# Output payloads persisted in tool_executions are capped (full output stays
# in the task checkpoint/executions rows capped separately).
_STORED_OUTPUT_CAP = 64 * 1024


@dataclass
class ToolRunOutcome:
    tool_key: str
    output: dict[str, Any]
    tool_execution_id: UUID
    duration_ms: int
    status: ToolRunStatus


class WorkspaceManager:
    """Per-task scratch directories under the sandbox root, with quotas."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(settings.sandbox_root).resolve()

    def ensure_task_workspace(self, task_id: UUID) -> Path:
        workspace = self.root / str(task_id)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def workspace_for(self, task_id: UUID) -> Path:
        return self.root / str(task_id)

    def quota_used_bytes(self, workspace: Path) -> int:
        total = 0
        for path in workspace.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:  # pragma: no cover
                    continue
        return total

    def enforce_quota(self, workspace: Path, additional_bytes: int) -> None:
        used = self.quota_used_bytes(workspace)
        limit = self.settings.sandbox_workspace_quota_bytes
        if used + additional_bytes > limit:
            raise ToolExecutionError(
                "workspace quota exceeded",
                details={
                    "used_bytes": used,
                    "limit_bytes": limit,
                    "requested_bytes": additional_bytes,
                },
            )


class ToolExecutor:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        registry: ToolRegistry,
    ) -> None:
        self.database = database
        self.settings = settings
        self.registry = registry
        self.workspace_manager = WorkspaceManager(settings)

    # -- policy -------------------------------------------------------------
    def _approval_required(self, tool: Tool, ctx: ToolContext) -> bool:
        if tool.requires_approval:
            return True
        if ctx.origin == "task":
            return tool.key not in self.settings.tool_auto_approved
        return False

    # -- execution ------------------------------------------------------------
    async def execute(
        self,
        *,
        tool_key: str,
        input_data: dict[str, Any],
        ctx: ToolContext,
        timeout_s: int | None = None,
    ) -> ToolRunOutcome:
        tool = self.registry.get(tool_key)

        if ctx.origin == "chat" and not tool.allowed_in_chat:
            raise AuthorizationError(
                f"tool '{tool_key}' is not permitted in conversational mode"
            )

        if self._approval_required(tool, ctx) and tool.key not in ctx.approved_tools:
            raise AuthorizationError(
                f"tool '{tool_key}' requires explicit user approval before execution"
            )

        if tool.sandboxed:
            if ctx.workspace is None:
                raise ToolExecutionError(
                    f"tool '{tool_key}' requires an execution workspace"
                )
            ctx.extra.setdefault("workspace_manager", self.workspace_manager)

        # deployment-level enable/disable
        await self._assert_enabled(tool_key)

        try:
            parsed = tool.InputModel.model_validate(input_data)
        except ValidationError as exc:
            raise RequestValidationError(
                f"invalid input for tool '{tool_key}'", details=_json_safe_errors(exc.errors())
            ) from exc

        execution_id = new_uuid()
        started = time.perf_counter()
        status = ToolRunStatus.RUNNING
        error_text: str | None = None
        output: dict[str, Any] | None = None
        try:
            timeout = min(
                timeout_s or tool.default_timeout_s,
                tool.max_timeout_s,
            )
            ctx.extra["timeout_s"] = timeout  # sandbox tools honour the step budget
            raw_output = await asyncio.wait_for(
                self._run_tool(tool, ctx, parsed.model_dump()), timeout=timeout + 5
            )
            try:
                validated = tool.OutputModel.model_validate(raw_output)
                output = validated.model_dump()
            except ValidationError as exc:
                raise ToolExecutionError(
                    f"tool '{tool_key}' produced invalid output",
                    details=_json_safe_errors(exc.errors()),
                ) from exc
            status = ToolRunStatus.SUCCEEDED
            telemetry.record_tool_execution(tool=tool_key, status="succeeded")
            return ToolRunOutcome(
                tool_key=tool_key,
                output=output,
                tool_execution_id=execution_id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                status=status,
            )
        except asyncio.TimeoutError:
            status = ToolRunStatus.TIMEOUT
            error_text = f"tool '{tool_key}' exceeded timeout"
            telemetry.record_tool_execution(tool=tool_key, status="timeout")
            logger.warning("tool_execution_timeout", tool=tool_key, task_id=str(ctx.task_id))
            raise ToolExecutionError(error_text) from None
        except Exception as exc:
            status = ToolRunStatus.FAILED
            error_text = f"{type(exc).__name__}: {exc}"
            telemetry.record_tool_execution(tool=tool_key, status="failed")
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            await self._record(
                execution_id=execution_id,
                tool=tool,
                ctx=ctx,
                input_data=_sanitize_input(input_data),
                output=(
                    _cap_stored(output, _STORED_OUTPUT_CAP) if output is not None else None
                ),
                status=status,
                error=error_text,
                duration_ms=duration_ms,
            )

    async def _run_tool(self, tool: Tool, ctx: ToolContext, input_data: dict[str, Any]) -> dict[str, Any]:
        """Run the tool with infrastructure services injected into the context."""
        ctx.extra.setdefault("settings", self.settings)
        async with self.database.session() as tool_session:
            ctx.extra.setdefault("db_session", tool_session)
            return await tool.run(ctx, input_data)

    # -- persistence -------------------------------------------------------------
    async def _assert_enabled(self, tool_key: str) -> None:
        async with self.database.session() as session:
            row = await session.scalar(
                sa.select(ToolDefinition.is_enabled).where(ToolDefinition.key == tool_key)
            )
            if row is False:
                raise AuthorizationError(f"tool '{tool_key}' is disabled on this deployment")

    async def _record(
        self,
        *,
        execution_id: UUID,
        tool: Tool,
        ctx: ToolContext,
        input_data: dict[str, Any],
        output: dict[str, Any] | None,
        status: ToolRunStatus,
        error: str | None,
        duration_ms: int,
    ) -> None:
        try:
            async with self.database.begin() as session:
                session.add(
                    ToolExecution(
                        id=execution_id,
                        user_id=ctx.user_id,
                        task_id=ctx.task_id,
                        tool_key=tool.key,
                        input=input_data,
                        output=output,
                        status=status,
                        error=error,
                        duration_ms=duration_ms,
                        sandbox="local" if tool.sandboxed else "none",
                        created_at=utcnow(),
                    )
                )
        except Exception:  # pragma: no cover - audit must not break execution
            logger.exception("tool_execution_record_failed", tool=tool.key)


def _sanitize_input(input_data: dict[str, Any]) -> dict[str, Any]:
    import json

    try:
        serialized = json.dumps(input_data, default=str)
    except Exception:  # pragma: no cover
        return {"unserializable": True}
    if len(serialized) > _STORED_OUTPUT_CAP:
        return {"truncated": True, "preview": serialized[:_STORED_OUTPUT_CAP]}
    return json.loads(serialized)


def _cap_stored(output: dict[str, Any], cap: int) -> dict[str, Any]:
    import json

    try:
        serialized = json.dumps(output, default=str)
    except Exception:  # pragma: no cover
        return {"unserializable": True}
    if len(serialized) <= cap:
        return json.loads(serialized)
    return {
        "truncated": True,
        "preview": serialized[:cap],
        "full_size_bytes": len(serialized),
    }
