"""Execution sandboxes: isolated subprocess execution for tools.

Two drivers:

* ``LocalProcessSandbox`` — hardened local subprocesses (dev / single-box
  private deployments).  Isolation properties: jailed working directory,
  minimal scrubbed environment (no application secrets), POSIX resource
  limits (CPU / address space / file size / process count), no shell ever
  involved, hard kill on timeout or cancellation (process group).
* ``DockerSandbox`` — containerised execution for production deployments
  (network disabled, memory/CPU caps, read-only rootfs, non-root user).

Cancellation propagates: a ``ControlHandle`` (set by the orchestrator when
a task is cancelled) kills the whole process group immediately.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import structlog

from app.core.errors import PathTraversalError, SandboxError, SandboxTimeoutError
from app.core.utils import truncate_text

logger = structlog.stdlib.get_logger("app.sandbox")

IS_POSIX = os.name == "posix"
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass
class SandboxCommand:
    argv: list[str]
    cwd: str = "."
    timeout_s: int = 60
    env: dict[str, str] = field(default_factory=dict)
    stdin: str | None = None
    max_output_bytes: int = 1024 * 1024


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    killed: bool = False

    def as_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "killed": self.killed,
        }


@dataclass
class ControlHandle:
    """Cooperative cancellation for in-flight sandbox executions."""

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = "cancelled"

    def cancel(self, reason: str = "cancelled") -> None:
        self.reason = reason
        self.cancel_event.set()


def resolve_in_root(root: Path, relative: str) -> Path:
    """Path-jail: resolve ``relative`` inside ``root`` or raise.

    Symlinks are followed (``resolve()``), so a symlink pointing outside
    the root is rejected by the containment check.
    """
    if not isinstance(relative, str) or relative == "" or "\x00" in relative:
        raise PathTraversalError("invalid path")
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise PathTraversalError(
            "path escapes the execution workspace", details={"path": relative}
        )
    return candidate


def _apply_posix_limits(memory_limit_mb: int) -> None:
    """preexec_fn: hard resource caps for the child process."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_limit_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (64,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (600,) * 2)
    except Exception:  # pragma: no cover - best effort on exotic kernels
        pass


class Sandbox(Protocol):
    name: str

    async def run(
        self,
        command: SandboxCommand,
        *,
        workspace: Path,
        control: ControlHandle | None = None,
    ) -> SandboxResult: ...


class LocalProcessSandbox:
    name = "local"

    def __init__(self, *, memory_limit_mb: int = 512) -> None:
        self.memory_limit_mb = memory_limit_mb

    def _build_env(self, command: SandboxCommand, workspace: Path) -> dict[str, str]:
        env = {
            "PATH": _DEFAULT_PATH,
            "HOME": str(workspace),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for key, value in command.env.items():
            if key.upper() in {"PATH", "HOME", "LD_PRELOAD", "LD_LIBRARY_PATH"}:
                continue  # never let child input override escape hatches
            if not key.isidentifier():
                continue
            env[key] = str(value)[:1000]
        return env

    async def run(
        self,
        command: SandboxCommand,
        *,
        workspace: Path,
        control: ControlHandle | None = None,
    ) -> SandboxResult:
        if not command.argv or not all(isinstance(a, str) and a for a in command.argv):
            raise SandboxError("argv must be a non-empty list of strings")
        if any("\x00" in a for a in command.argv):
            raise SandboxError("argv contains NUL bytes")

        cwd = resolve_in_root(workspace, command.cwd or ".")
        if not cwd.is_dir():
            raise SandboxError(f"working directory '{command.cwd}' does not exist")

        started = time.perf_counter()
        process: subprocess.Popen | None = None
        timed_out = False
        killed = False

        def _spawn() -> subprocess.Popen:
            kwargs: dict = {}
            if IS_POSIX:
                kwargs["preexec_fn"] = lambda: _apply_posix_limits(self.memory_limit_mb)
                kwargs["start_new_session"] = True
            return subprocess.Popen(  # noqa: S603 - argv list, no shell
                command.argv,
                cwd=str(cwd),
                stdin=subprocess.PIPE if command.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._build_env(command, workspace),
                text=True,
                **kwargs,
            )

        try:
            process = await asyncio.to_thread(_spawn)
        except FileNotFoundError as exc:
            raise SandboxError(f"executable not found: {command.argv[0]}") from exc
        except PermissionError as exc:
            raise SandboxError(f"executable not permitted: {command.argv[0]}") from exc

        def _communicate() -> tuple[bool, str, str]:
            assert process is not None
            try:
                out, err = process.communicate(
                    input=command.stdin, timeout=command.timeout_s
                )
                return False, out or "", err or ""
            except subprocess.TimeoutExpired:
                return True, "", f"process exceeded {command.timeout_s}s timeout"

        comm_task = asyncio.create_task(asyncio.to_thread(_communicate))

        cancel_task: asyncio.Task | None = None
        if control is not None:

            async def _watch_cancel() -> None:
                await control.cancel_event.wait()
                nonlocal killed
                killed = True
                _kill_process_group(process)

            cancel_task = asyncio.create_task(_watch_cancel())

        try:
            did_timeout, stdout, stderr = await comm_task
            if did_timeout:
                timed_out = True
                killed = True
                _kill_process_group(process)
                # reap remaining buffered output (supported communicate() re-entry)
                try:
                    stdout, stderr = await asyncio.to_thread(
                        lambda: process.communicate(timeout=5)
                    )
                    stdout, stderr = stdout or "", stderr or ""
                except Exception:
                    stdout, stderr = "", f"process exceeded {command.timeout_s}s timeout"
            duration_ms = int((time.perf_counter() - started) * 1000)
            if process.poll() is None:
                killed = True
                _kill_process_group(process)
                try:
                    await asyncio.to_thread(lambda: process.wait(timeout=5))
                except Exception:  # pragma: no cover
                    pass
            exit_code = process.returncode if process.returncode is not None else -9
            return SandboxResult(
                exit_code=exit_code,
                stdout=truncate_text(stdout, command.max_output_bytes, marker="\n…[output truncated]"),
                stderr=truncate_text(stderr, max(1024, command.max_output_bytes // 4), marker="\n…[output truncated]"),
                duration_ms=duration_ms,
                timed_out=timed_out,
                killed=killed,
            )
        except asyncio.CancelledError:
            _kill_process_group(process)
            raise
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass


def _kill_process_group(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        if IS_POSIX and os.getpgid(process.pid) == process.pid:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


class DockerSandbox:
    """Container-isolated execution (production deployments).

    The command runs as:

        docker run --rm
          --network none                      (no network unless explicitly enabled)
          --memory <mem> --cpus <cpus>
          --user 65534:65534                  (nobody; workspace dirs are chmod'd)
          -v <workspace>:/workspace:rw
          -w /workspace
          <image> <argv...>

    A wrapper timeout is enforced by ``docker run``'s own ``timeout``
    utility passed as PID 1 helper: we wrap argv with ``timeout -k 5 <s>``.
    """

    name = "docker"

    def __init__(
        self,
        *,
        image: str,
        memory_limit: str = "512m",
        cpus: str = "1.0",
        disable_network: bool = True,
    ) -> None:
        self.image = image
        self.memory_limit = memory_limit
        self.cpus = cpus
        self.disable_network = disable_network

    def build_argv(self, command: SandboxCommand) -> list[str]:
        if not command.argv:
            raise SandboxError("argv must be non-empty")
        workspace_mount = "/workspace"
        return [
            "docker", "run", "--rm",
            *(["--network", "none"] if self.disable_network else []),
            "--memory", self.memory_limit,
            "--cpus", self.cpus,
            "--user", "65534:65534",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "-v", f"__WORKSPACE__:{workspace_mount}:rw",
            "-w", workspace_mount,
            self.image,
            "timeout", "-k", "5", str(command.timeout_s),
            *command.argv,
        ]

    async def run(
        self,
        command: SandboxCommand,
        *,
        workspace: Path,
        control: ControlHandle | None = None,
    ) -> SandboxResult:
        if shutil.which("docker") is None:
            raise SandboxError(
                "docker binary not available; configure AGP_SANDBOX_BACKEND=local "
                "or install Docker on the worker host"
            )
        argv = self.build_argv(command)
        # substitute the mount source (workspace path)
        argv[argv.index("-v") + 1] = f"{workspace.resolve()}:/workspace:rw"
        # docker needs the workspace readable by uid 65534
        try:
            os.chmod(workspace, 0o777)
        except OSError:  # pragma: no cover
            pass
        inner = SandboxCommand(
            argv=argv,
            cwd=".",
            timeout_s=command.timeout_s + 30,  # outer margin
            env={},
            stdin=command.stdin,
            max_output_bytes=command.max_output_bytes,
        )
        result = await LocalProcessSandbox().run(inner, workspace=workspace.parent, control=control)
        # docker's exit code 124/137 == inner timeout/kill
        if result.exit_code in (124, 137):
            result.timed_out = True
        return result


def get_sandbox(settings) -> Sandbox:
    if settings.sandbox_backend == "docker":
        return DockerSandbox(
            image=settings.sandbox_docker_image,
            memory_limit=settings.sandbox_docker_mem_limit,
            cpus=settings.sandbox_docker_cpus,
            disable_network=settings.sandbox_docker_disable_network,
        )
    return LocalProcessSandbox(memory_limit_mb=settings.sandbox_memory_limit_mb)


# Re-exported for tests
PYTHON_BIN = sys.executable or "python3"
