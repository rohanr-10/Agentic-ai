"""Filesystem tools — hard-jailed to the task workspace."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.core.errors import RequestValidationError, ToolExecutionError
from app.tools.base import Tool, ToolContext
from app.tools.sandbox import resolve_in_root

_MAX_READ = 512 * 1024
_MAX_WRITE = 512 * 1024
_MAX_LIST = 500
_MAX_SEARCH_RESULTS = 100
_MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024


class FsReadInput(BaseModel):
    path: str = Field(description="Relative path inside the task workspace")
    max_bytes: int = Field(default=65536, ge=1, le=_MAX_READ)


class FsReadOutput(BaseModel):
    path: str
    content: str
    size_bytes: int
    truncated: bool


class FsReadTool(Tool):
    key = "fs.read"
    name = "Read workspace file"
    description = "Read a UTF-8 text file from the task workspace."
    category = "filesystem"
    permissions: ClassVar[list[str]] = ["fs:read"]
    InputModel = FsReadInput
    OutputModel = FsReadOutput
    default_timeout_s = 15
    max_timeout_s = 30

    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        target = resolve_in_root(ctx.workspace, raw_input["path"])
        if not target.is_file():
            raise ToolExecutionError(f"file not found: {raw_input['path']}")

        def _read() -> bytes:
            with open(target, "rb") as fh:
                return fh.read(raw_input["max_bytes"] + 1)

        data = await asyncio.to_thread(_read)
        truncated = len(data) > raw_input["max_bytes"]
        content = data[: raw_input["max_bytes"]].decode("utf-8", errors="replace")
        return {
            "path": raw_input["path"],
            "content": content,
            "size_bytes": target.stat().st_size,
            "truncated": truncated,
        }


class FsWriteInput(BaseModel):
    path: str = Field(description="Relative path inside the task workspace")
    content: str = Field(max_length=_MAX_WRITE)
    mode: str = Field(default="write", pattern="^(write|append)$")


class FsWriteOutput(BaseModel):
    path: str
    bytes_written: int


class FsWriteTool(Tool):
    key = "fs.write"
    name = "Write workspace file"
    description = "Write (or append to) a text file inside the task workspace."
    category = "filesystem"
    permissions: ClassVar[list[str]] = ["fs:write"]
    InputModel = FsWriteInput
    OutputModel = FsWriteOutput
    default_timeout_s = 15
    max_timeout_s = 30

    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        from app.tools.executor import WorkspaceManager

        workspace_manager: WorkspaceManager | None = ctx.extra.get("workspace_manager")
        if workspace_manager is not None:
            workspace_manager.enforce_quota(ctx.workspace, len(raw_input["content"].encode()))

        target = resolve_in_root(ctx.workspace, raw_input["path"])
        # refuse to write through existing directories
        if target.is_dir():
            raise ToolExecutionError(f"path is a directory: {raw_input['path']}")

        def _write() -> int:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = raw_input["content"].encode("utf-8")
            with open(target, "ab" if raw_input["mode"] == "append" else "wb") as fh:
                fh.write(data)
                return len(data)

        written = await asyncio.to_thread(_write)
        return {"path": raw_input["path"], "bytes_written": written}


class FsListInput(BaseModel):
    path: str = Field(default=".")
    limit: int = Field(default=100, ge=1, le=_MAX_LIST)
    recursive: bool = False


class FsListOutput(BaseModel):
    path: str
    entries: list[dict]
    count: int


class FsListTool(Tool):
    key = "fs.list"
    name = "List workspace directory"
    description = "List files and directories inside the task workspace."
    category = "filesystem"
    permissions: ClassVar[list[str]] = ["fs:read"]
    InputModel = FsListInput
    OutputModel = FsListOutput
    default_timeout_s = 15
    max_timeout_s = 30

    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        base = resolve_in_root(ctx.workspace, raw_input["path"])
        if not base.is_dir():
            raise ToolExecutionError(f"not a directory: {raw_input['path']}")

        def _list() -> list[Path]:
            if raw_input["recursive"]:
                return sorted(p for p in base.rglob("*") if p.is_file())
            return sorted(base.iterdir())

        paths = await asyncio.to_thread(_list)
        entries: list[dict] = []
        for path in paths[: raw_input["limit"]]:
            relative = path.relative_to(ctx.workspace).as_posix()
            if path.is_dir():
                entries.append({"name": path.name, "path": relative, "type": "directory"})
            else:
                try:
                    size = path.stat().st_size
                except OSError:  # pragma: no cover
                    size = 0
                entries.append({"name": path.name, "path": relative, "type": "file", "size_bytes": size})
        return {"path": raw_input["path"], "entries": entries, "count": len(entries)}


class CodeSearchInput(BaseModel):
    pattern: str = Field(description="Regular expression to search for", max_length=500)
    path: str = Field(default=".")
    max_results: int = Field(default=30, ge=1, le=_MAX_SEARCH_RESULTS)
    ignore_case: bool = True


class CodeSearchOutput(BaseModel):
    pattern: str
    matches: list[dict]
    count: int


class CodeSearchTool(Tool):
    key = "code.search"
    name = "Search code in workspace"
    description = "Regex search across text files in the task workspace."
    category = "development"
    permissions: ClassVar[list[str]] = ["fs:read"]
    InputModel = CodeSearchInput
    OutputModel = CodeSearchOutput
    default_timeout_s = 30
    max_timeout_s = 60

    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        try:
            pattern = re.compile(
                raw_input["pattern"], re.IGNORECASE if raw_input["ignore_case"] else 0
            )
        except re.error as exc:
            raise RequestValidationError(f"invalid regex: {exc}") from exc

        base = resolve_in_root(ctx.workspace, raw_input["path"])
        if not base.is_dir():
            raise ToolExecutionError(f"not a directory: {raw_input['path']}")

        def _search() -> list[dict]:
            matches: list[dict] = []
            for path in sorted(base.rglob("*")):
                if not path.is_file() or path.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                    continue
                try:
                    text = path.read_text("utf-8", errors="replace")
                except OSError:
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if pattern.search(line):
                        matches.append(
                            {
                                "file": path.relative_to(ctx.workspace).as_posix(),
                                "line": line_no,
                                "text": line.strip()[:300],
                            }
                        )
                        if len(matches) >= raw_input["max_results"]:
                            return matches
            return matches

        matches = await asyncio.to_thread(_search)
        return {"pattern": raw_input["pattern"], "matches": matches, "count": len(matches)}
