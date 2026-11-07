"""Tool contracts.

Every tool is a small, schema-validated unit of work.  Tools receive a
``ToolContext`` describing WHO is executing (user), WHERE (workspace path,
optional), and WHAT has been pre-approved.  Tools never touch the database
directly (except read-only file lookups via injected services) and never
see application secrets.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel

from app.tools.sandbox import ControlHandle


@dataclass
class ToolContext:
    user_id: UUID
    task_id: UUID | None = None
    """Workspace directory for sandboxed tools (None for service tools)."""
    workspace: Path | None = None
    """Tool keys explicitly approved for this invocation (approval workflow)."""
    approved_tools: set[str] = field(default_factory=set)
    """Invocation context: 'task' (autonomous) or 'chat' (conversational)."""
    origin: str = "task"
    control: ControlHandle | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Tool(abc.ABC):
    """Base class for all registered tools."""

    key: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[str]
    permissions: ClassVar[list[str]] = []
    InputModel: ClassVar[type[BaseModel]]
    OutputModel: ClassVar[type[BaseModel]]
    default_timeout_s: ClassVar[int] = 60
    max_timeout_s: ClassVar[int] = 300
    """Sensitive tools require explicit approval inside autonomous tasks."""
    requires_approval: ClassVar[bool] = False
    """Sandboxed tools operate inside the task workspace."""
    sandboxed: ClassVar[bool] = True
    """Whether the model may call this tool during plain conversations."""
    allowed_in_chat: ClassVar[bool] = False

    @abc.abstractmethod
    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        """Execute with validated input; returns output dict matching OutputModel."""

    def input_json_schema(self) -> dict[str, Any]:
        return self.InputModel.model_json_schema()

    def output_json_schema(self) -> dict[str, Any]:
        return self.OutputModel.model_json_schema()

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "permissions": list(self.permissions),
            "input_schema": self.input_json_schema(),
            "output_schema": self.output_json_schema(),
            "default_timeout_s": self.default_timeout_s,
            "max_timeout_s": self.max_timeout_s,
            "requires_approval": self.requires_approval,
            "allowed_in_chat": self.allowed_in_chat,
        }
