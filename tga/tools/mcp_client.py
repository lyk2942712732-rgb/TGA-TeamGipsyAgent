from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tga.tools.mcp_catalog import MCPServerSpec
from tga.tools.mcp_config import MCPServerConfig
from tga.tools.mcp_transport import build_stdio_command, build_subprocess_environment


@dataclass
class MCPCallResult:
    command: list[str]
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False
    output_truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class MCPClient:
    """Compatibility stdio client for legacy specs and explicit commands.

    Product discovery/lifecycle is owned by :class:`MCPManager`; this facade
    keeps older ToolRunner integrations working while accepting the same
    host-controlled ``MCPServerConfig`` command/args model.
    """

    def __init__(self, *, hub_root: str | Path | None = None, prefer_compose: bool = True):
        self.hub_root = Path(hub_root).resolve() if hub_root else None
        self.prefer_compose = prefer_compose

    def build_command(
        self, server: MCPServerSpec | MCPServerConfig, *, volumes: list[str] | None = None, workspace: Path | None = None
    ) -> list[str]:
        volumes = volumes or []
        if isinstance(server, MCPServerConfig):
            if volumes:
                raise ValueError("configured MCP volumes must come from workspaceMount, not call arguments")
            return build_stdio_command(server, workspace=workspace)
        if self.prefer_compose and self.hub_root and server.compose_service:
            compose_file = self.hub_root / "docker-compose.yml"
            if compose_file.exists():
                command = ["docker", "compose", "-f", str(compose_file)]
                for profile in server.profiles:
                    command.extend(["--profile", profile])
                command.extend(["run", "--rm", "-T"])
                for volume in volumes:
                    command.extend(["--volume", volume])
                command.append(server.compose_service)
                return command

        command = ["docker", "run", "-i", "--rm"]
        for volume in volumes:
            command.extend(["-v", volume])
        command.append(server.image)
        return command

    def call_tool(
        self,
        *,
        server: MCPServerSpec | MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
        volumes: list[str] | None = None,
        timeout_seconds: int | None = None,
        max_output_bytes: int | None = None,
        workspace: Path | None = None,
    ) -> MCPCallResult:
        timeout_seconds = timeout_seconds or (server.tool_timeout_seconds if isinstance(server, MCPServerConfig) else 120)
        max_output_bytes = max_output_bytes or (server.max_output_bytes if isinstance(server, MCPServerConfig) else 262_144)
        try:
            command = self.build_command(server, volumes=volumes, workspace=workspace)
        except TypeError:
            # Preserve third-party subclasses written before the generic
            # workspace-aware signature.
            command = self.build_command(server, volumes=volumes)
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "tga", "version": "0.1.0"},
            },
        }
        initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=build_subprocess_environment(server.environment) if isinstance(server, MCPServerConfig) else None,
            )
        except OSError as exc:
            return MCPCallResult(command, "", str(exc), returncode=127)

        stdout_queue: queue.Queue[str] = queue.Queue()
        stderr_queue: queue.Queue[str] = queue.Queue()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        _start_reader(process.stdout, stdout_queue)
        _start_reader(process.stderr, stderr_queue)

        deadline = time.monotonic() + timeout_seconds
        try:
            _send_json(process, initialize)
            if not _wait_for_response(stdout_queue, stdout_lines, expected_id=1, deadline=deadline):
                return _finish_timeout(process, command, stdout_lines, stderr_lines, stderr_queue, max_output_bytes)
            _drain_queue(stderr_queue, stderr_lines)

            _send_json(process, initialized)
            _send_json(process, call)
            if not _wait_for_response(stdout_queue, stdout_lines, expected_id=2, deadline=deadline):
                return _finish_timeout(process, command, stdout_lines, stderr_lines, stderr_queue, max_output_bytes)

            if process.stdin:
                process.stdin.close()
            try:
                returncode = process.wait(timeout=max(1.0, min(5.0, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                process.terminate()
                returncode = process.wait(timeout=5)
            _drain_queue(stdout_queue, stdout_lines)
            _drain_queue(stderr_queue, stderr_lines)
            return _bounded_result(command, stdout_lines, stderr_lines, returncode, max_output_bytes)
        except (BrokenPipeError, OSError) as exc:
            _drain_queue(stdout_queue, stdout_lines)
            _drain_queue(stderr_queue, stderr_lines)
            stderr_lines.append(str(exc))
            return _bounded_result(command, stdout_lines, stderr_lines, returncode=1, max_output_bytes=max_output_bytes)


def _start_reader(stream: Any, output: queue.Queue[str]) -> None:
    def read_stream() -> None:
        if stream is None:
            return
        for line in stream:
            output.put(line)

    threading.Thread(target=read_stream, daemon=True).start()


def _send_json(process: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise BrokenPipeError("process stdin is closed")
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()


def _wait_for_response(
    output: queue.Queue[str],
    collected: list[str],
    *,
    expected_id: int,
    deadline: float,
) -> bool:
    while time.monotonic() < deadline:
        try:
            line = output.get(timeout=0.1)
        except queue.Empty:
            continue
        collected.append(line)
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == expected_id:
            return True
    return False


def _drain_queue(output: queue.Queue[str], collected: list[str]) -> None:
    while True:
        try:
            collected.append(output.get_nowait())
        except queue.Empty:
            return


def _finish_timeout(
    process: subprocess.Popen[str],
    command: list[str],
    stdout_lines: list[str],
    stderr_lines: list[str],
    stderr_queue: queue.Queue[str],
    max_output_bytes: int,
) -> MCPCallResult:
    _drain_queue(stderr_queue, stderr_lines)
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    result = _bounded_result(command, stdout_lines, stderr_lines, returncode=124, max_output_bytes=max_output_bytes)
    result.timed_out = True
    return result


def _bounded_result(
    command: list[str], stdout_lines: list[str], stderr_lines: list[str], returncode: int, max_output_bytes: int
) -> MCPCallResult:
    stdout, stdout_truncated = _bounded_text("".join(stdout_lines), max_output_bytes)
    stderr, stderr_truncated = _bounded_text("".join(stderr_lines), max_output_bytes)
    return MCPCallResult(command, stdout, stderr, returncode, output_truncated=stdout_truncated or stderr_truncated)


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="replace"), True
