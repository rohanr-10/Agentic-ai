"""Code and terminal execution tools (sandbox-isolated)."""

from __future__ import annotations

import shutil
import sys
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.core.errors import RequestValidationError, ToolExecutionError
from app.tools.base import Tool, ToolContext
from app.tools.sandbox import SandboxCommand, get_sandbox

PYTHON_BIN = sys.executable or "python3"


class _ExecOutput(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool


class PythonExecuteInput(BaseModel):
    code: str = Field(max_length=512 * 1024, description="Python source code to execute")
    timeout_s: int = Field(default=60, ge=1, le=600)
    stdin: str | None = Field(default=None, max_length=64 * 1024)


class PythonExecuteTool(Tool):
    key = "python.execute"
    name = "Execute Python code"
    description = (
        "Run Python code in the task's isolated sandbox workspace "
        "(fresh interpreter, no network by default in docker mode, resource limits)."
    )
    category = "execution"
    permissions: ClassVar[list[str]] = ["exec:python"]
    InputModel = PythonExecuteInput
    OutputModel = _ExecOutput
    default_timeout_s = 60
    max_timeout_s = 600
    allowed_in_chat: ClassVar[bool] = False

    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        workspace = ctx.workspace
        run_counter = ctx.extra.get("_python_run_counter", 0) + 1
        ctx.extra["_python_run_counter"] = run_counter
        script = workspace / f".agp_runs/script_{run_counter}.py"

        def _write() -> None:
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(raw_input["code"], encoding="utf-8")

        import asyncio

        await asyncio.to_thread(_write)

        sandbox = get_sandbox(ctx.extra.get("settings"))
        # effective timeout: the caller's budget (step timeout) unless the
        # input explicitly requests less
        timeout_s = min(
            raw_input.get("timeout_s", 60),
            int(ctx.extra.get("timeout_s", raw_input.get("timeout_s", 60))),
        )
        command = SandboxCommand(
            argv=[PYTHON_BIN, "-I", str(script)],
            cwd=".",
            timeout_s=timeout_s,
            stdin=raw_input.get("stdin"),
        )
        result = await sandbox.run(command, workspace=workspace, control=ctx.control)
        _raise_on_failure(result)
        return result.as_dict()


class TerminalExecuteInput(BaseModel):
    command: list[str] = Field(
        min_length=1, max_length=50, description="argv list (no shell involved)"
    )
    timeout_s: int = Field(default=30, ge=1, le=300)
    cwd: str = Field(default=".")


class TerminalExecuteTool(Tool):
    key = "terminal.execute"
    name = "Execute allowlisted terminal command"
    description = (
        "Run a command from the deployment allowlist in the task sandbox. "
        "Arguments are passed directly to exec (no shell parsing)."
    )
    category = "execution"
    permissions: ClassVar[list[str]] = ["exec:terminal"]
    InputModel = TerminalExecuteInput
    OutputModel = _ExecOutput
    default_timeout_s = 30
    max_timeout_s = 300
    requires_approval: ClassVar[bool] = True
    allowed_in_chat: ClassVar[bool] = False

    async def run(self, ctx: ToolContext, raw_input: dict[str, Any]) -> dict[str, Any]:
        argv = [str(a) for a in raw_input["command"]]
        if any("\x00" in a for a in argv):
            raise RequestValidationError("command arguments contain NUL bytes")

        settings = ctx.extra.get("settings")
        allowed = (
            settings.terminal_allowed_binaries
            if settings is not None
            else ["ls", "cat", "head", "tail", "wc", "grep"]
        )

        program = argv[0]
        if "/" in program or program.startswith("."):
            raise RequestValidationError(
                "command must be a bare program name from the allowlist",
                details={"allowlist": list(allowed)},
            )
        if program not in allowed:
            raise RequestValidationError(
                f"command '{program}' is not in the allowlist",
                details={"allowlist": list(allowed)},
            )
        resolved = shutil.which(program)
        if resolved is None:
            raise ToolExecutionError(f"allowed command '{program}' is not installed")

        sandbox = get_sandbox(settings)
        timeout_s = min(
            raw_input.get("timeout_s", 30),
            int(ctx.extra.get("timeout_s", raw_input.get("timeout_s", 30))),
        )
        command = SandboxCommand(
            argv=[resolved, *argv[1:]],
            cwd=raw_input.get("cwd", "."),
            timeout_s=timeout_s,
        )
        result = await sandbox.run(command, workspace=ctx.workspace, control=ctx.control)
        _raise_on_failure(result)
        return result.as_dict()


def _raise_on_failure(result) -> None:
    """Non-zero exits and timeouts are tool failures, never silent successes."""
    from app.core.errors import ToolRetryableError

    if result.timed_out:
        raise ToolRetryableError(
            f"process timed out after {result.duration_ms}ms",
            details={"stderr": result.stderr[:500]},
        )
    if result.exit_code != 0:
        raise ToolExecutionError(
            f"process exited with code {result.exit_code}",
            details={"stderr": result.stderr[:1000], "stdout": result.stdout[:1000]},
        )
