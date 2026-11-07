"""Unit tests: local sandbox isolation + path jail + docker command build."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.core.errors import PathTraversalError, SandboxError
from app.tools.sandbox import (
    DockerSandbox,
    LocalProcessSandbox,
    SandboxCommand,
    resolve_in_root,
)


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sub").mkdir()
    (ws / "hello.txt").write_text("hello world")
    return ws


class TestPathJail:
    def test_normal_relative_path_allowed(self, workspace):
        target = resolve_in_root(workspace, "sub/../hello.txt")
        assert target.name == "hello.txt"

    @pytest.mark.parametrize(
        "evil",
        ["../escape.txt", "sub/../../etc/passwd", "/etc/passwd", "a/../../../root/x"],
    )
    def test_traversal_blocked(self, workspace, evil):
        with pytest.raises(PathTraversalError):
            resolve_in_root(workspace, evil)

    def test_symlink_escape_blocked(self, workspace):
        link = workspace / "link"
        link.symlink_to(workspace.parent / "outside.txt")
        (workspace.parent / "outside.txt").write_text("secret")
        with pytest.raises(PathTraversalError):
            resolve_in_root(workspace, "link")

    def test_null_byte_rejected(self, workspace):
        with pytest.raises(PathTraversalError):
            resolve_in_root(workspace, "evil\x00.txt")


class TestLocalSandbox:
    async def test_simple_command(self, workspace):
        sandbox = LocalProcessSandbox()
        result = await sandbox.run(
            SandboxCommand(argv=[sys.executable, "-c", "print('sandbox ok')"], timeout_s=15),
            workspace=workspace,
        )
        assert result.exit_code == 0
        assert "sandbox ok" in result.stdout
        assert not result.timed_out

    async def test_environment_is_scrubbed(self, workspace):
        """No application secrets leak into child processes."""
        os.environ["AGP_SECRET_KEY"] = "super-secret-value"
        os.environ["DATABASE_URL"] = "postgres://user:pass@db"
        try:
            sandbox = LocalProcessSandbox()
            result = await sandbox.run(
                SandboxCommand(
                    argv=[
                        sys.executable,
                        "-c",
                        "import os; print(sorted(k for k in os.environ "
                        "if 'SECRET' in k or 'DATABASE' in k or 'TOKEN' in k))",
                    ],
                    timeout_s=15,
                ),
                workspace=workspace,
            )
            assert result.exit_code == 0
            assert "super-secret-value" not in result.stdout
            assert "DATABASE_URL" not in result.stdout
        finally:
            os.environ.pop("AGP_SECRET_KEY", None)
            os.environ.pop("DATABASE_URL", None)

    async def test_timeout_kills_process(self, workspace):
        sandbox = LocalProcessSandbox()
        result = await sandbox.run(
            SandboxCommand(argv=[sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=1),
            workspace=workspace,
        )
        assert result.timed_out
        assert result.exit_code != 0

    async def test_cwd_must_stay_in_workspace(self, workspace):
        sandbox = LocalProcessSandbox()
        with pytest.raises((PathTraversalError, SandboxError)):
            await sandbox.run(
                SandboxCommand(argv=["/bin/true"], cwd="../outside", timeout_s=5),
                workspace=workspace,
            )

    async def test_missing_binary_raises(self, workspace):
        sandbox = LocalProcessSandbox()
        with pytest.raises(SandboxError):
            await sandbox.run(
                SandboxCommand(argv=["/nonexistent/binary"], timeout_s=5),
                workspace=workspace,
            )

    async def test_cancellation_kills_process(self, workspace):
        """ControlHandle cancellation terminates the process group quickly."""
        import asyncio
        import time

        from app.tools.sandbox import ControlHandle

        sandbox = LocalProcessSandbox()
        control = ControlHandle()
        start = time.monotonic()
        task = asyncio.create_task(
            sandbox.run(
                SandboxCommand(argv=[sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=30),
                workspace=workspace,
                control=control,
            )
        )
        await asyncio.sleep(0.4)
        control.cancel("test cancel")
        result = await asyncio.wait_for(task, timeout=5)
        assert result.killed
        assert time.monotonic() - start < 5

    async def test_output_truncated(self, workspace):
        sandbox = LocalProcessSandbox()
        result = await sandbox.run(
            SandboxCommand(
                argv=[sys.executable, "-c", "print('x' * 100_000)"],
                timeout_s=15,
                max_output_bytes=1000,
            ),
            workspace=workspace,
        )
        assert len(result.stdout) <= 1200

    async def test_no_shell_interpretation(self, workspace):
        """argv is exec'd directly; shell metacharacters have no effect."""
        sandbox = LocalProcessSandbox()
        result = await sandbox.run(
            SandboxCommand(argv=["/bin/echo", "hello; rm -rf /"], timeout_s=10),
            workspace=workspace,
        )
        assert result.exit_code == 0
        assert "hello; rm -rf /" in result.stdout


class TestDockerSandboxCommand:
    def test_command_construction(self):
        sandbox = DockerSandbox(image="python:3.12-slim", disable_network=True)
        argv = sandbox.build_argv(SandboxCommand(argv=["python", "-c", "print(1)"], timeout_s=10))
        assert argv[0] == "docker"
        assert "--network" in argv and "none" in argv
        assert "--cap-drop" in argv and "ALL" in argv
        assert "--security-opt" in argv and "no-new-privileges" in argv
        assert "python:3.12-slim" in argv
        # timeout wrapper: image timeout -k 5 <timeout_s> <payload...>
        payload_index = argv.index("python:3.12-slim")
        assert argv[payload_index + 1 : payload_index + 5] == ["timeout", "-k", "5", "10"]
        assert argv[payload_index + 5 :] == ["python", "-c", "print(1)"]

    def test_network_can_be_enabled_for_trusted_deployments(self):
        sandbox = DockerSandbox(image="img", disable_network=False)
        argv = sandbox.build_argv(SandboxCommand(argv=["x"], timeout_s=5))
        assert "--network" not in argv
