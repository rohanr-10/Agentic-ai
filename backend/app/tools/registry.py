"""Tool registry.

The in-code registry is the source of truth for tool behaviour and schemas;
the ``tool_definitions`` table mirrors it for per-deployment enable/disable
state and for showing tool metadata to users (and to models) via the API.
"""

from __future__ import annotations

from typing import Iterable

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.utils import new_uuid
from app.db.models import ToolDefinition
from app.tools.base import Tool

logger = structlog.stdlib.get_logger("app.tools")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.key in self._tools:
            raise ValueError(f"tool '{tool.key}' already registered")
        self._tools[tool.key] = tool
        return tool

    def get(self, key: str) -> Tool:
        tool = self._tools.get(key)
        if tool is None:
            raise NotFoundError(f"unknown tool '{key}'")
        return tool

    def try_get(self, key: str) -> Tool | None:
        return self._tools.get(key)

    def all_tools(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda t: t.key)

    def keys(self) -> list[str]:
        return sorted(self._tools.keys())

    def chat_tools(self) -> list[Tool]:
        return [t for t in self.all_tools() if t.allowed_in_chat]

    def __len__(self) -> int:
        return len(self._tools)


_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _registry


def reset_registry_for_tests() -> None:
    """Test helper: rebuild a registry containing only built-in tools."""
    global _registry
    _registry = ToolRegistry()
    register_builtin_tools(_registry)


async def ensure_seeded(session: AsyncSession, registry: ToolRegistry) -> None:
    """Idempotently mirror registered tools into ``tool_definitions``.

    Existing rows keep their deployment state (``is_enabled``) — code is the
    source of truth for schema/behaviour, the row stores deployment policy.
    """
    existing = {
        row.key: row
        for row in (
            await session.execute(sa.select(ToolDefinition))
        ).scalars().all()
    }
    for tool in registry.all_tools():
        row = existing.get(tool.key)
        description = tool.describe()
        if row is None:
            session.add(
                ToolDefinition(
                    id=new_uuid(),
                    key=tool.key,
                    name=tool.name,
                    description=tool.description,
                    category=tool.category,
                    input_schema=description["input_schema"],
                    output_schema=description["output_schema"],
                    permissions=description["permissions"],
                    default_timeout_s=tool.default_timeout_s,
                    max_timeout_s=tool.max_timeout_s,
                    is_builtin=True,
                    is_enabled=True,
                )
            )
        else:
            row.name = tool.name
            row.description = tool.description
            row.category = tool.category
            row.input_schema = description["input_schema"]
            row.output_schema =description["output_schema"]
            row.permissions = description["permissions"]
            row.default_timeout_s = tool.default_timeout_s
            row.max_timeout_s = tool.max_timeout_s
    # flag tool rows that no longer exist in code
    known = set(registry.keys())
    for key, row in existing.items():
        if key not in known:
            row.is_enabled = False
    await session.commit()


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register all built-in tools (idempotent-ish: skips existing)."""
    from app.tools.builtin.docs import DataAnalyzeCsvTool, DocumentExtractTool
    from app.tools.builtin.exec import PythonExecuteTool, TerminalExecuteTool
    from app.tools.builtin.fs import (
        CodeSearchTool,
        FsListTool,
        FsReadTool,
        FsWriteTool,
    )
    from app.tools.builtin.http import HttpRequestTool

    for tool in (
        FsReadTool(),
        FsWriteTool(),
        FsListTool(),
        CodeSearchTool(),
        HttpRequestTool(),
        PythonExecuteTool(),
        TerminalExecuteTool(),
        DocumentExtractTool(),
        DataAnalyzeCsvTool(),
    ):
        if tool.key not in registry.keys():
            registry.register(tool)
    logger.info("builtin_tools_registered", count=len(registry))


def tool_summaries_for_model(registry: ToolRegistry, keys: Iterable[str]) -> list[dict]:
    """Compact tool descriptions for model prompts."""
    summaries = []
    for key in keys:
        tool = registry.try_get(key)
        if tool is None:
            continue
        schema = tool.input_json_schema()
        summaries.append(
            {
                "name": tool.key,
                "description": tool.description,
                "input_schema": schema,
            }
        )
    return summaries
