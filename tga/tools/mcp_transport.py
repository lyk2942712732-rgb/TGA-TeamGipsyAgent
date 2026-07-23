"""Transport abstraction for MCP JSON-RPC connections."""

from __future__ import annotations

import json
import os
import queue
import ssl
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from tga.tools.mcp_config import MCPServerConfig


AUTOMATIC_WORKSPACE_CONTAINER_PATH = "/workspace"
AUTOMATIC_ARTIFACTS_CONTAINER_PATH = "/workspace/artifacts"


class MCPTransportError(RuntimeError):
    def __init__(self, message: str, *, code: str = "MCP_TRANSPORT_ERROR", phase: str = "transport", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.retryable = retryable


class MCPTransport(Protocol):
    connected: bool
    output_truncated: bool

    def connect(self) -> None: ...
    def send(self, message: dict[str, Any]) -> None: ...
    def receive(self, timeout: float) -> dict[str, Any]: ...
    def close(self) -> None: ...
    def finish(self, timeout: float = 2.0) -> int | None: ...

    @property
    def stdout_text(self) -> str: ...
    @property
    def stderr_text(self) -> str: ...
    @property
    def returncode(self) -> int | None: ...


class StdioTransport:
    """One subprocess per connection with strict stdout/stderr separation."""

    def __init__(self, command: list[str], *, environment: dict[str, str], max_capture_bytes: int) -> None:
        self.command = command
        self.environment = environment
        self.max_capture_bytes = max_capture_bytes
        self.process: subprocess.Popen[str] | None = None
        self.connected = False
        self._stdout: queue.Queue[str | None] = queue.Queue()
        self._stderr: queue.Queue[str | None] = queue.Queue()
        self._stdout_capture = tempfile.TemporaryFile(mode="w+b")
        self._stderr_capture = tempfile.TemporaryFile(mode="w+b")
        self._stdout_bytes = 0
        self._stderr_bytes = 0
        self._stdout_lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self.output_truncated = False

    def connect(self) -> None:
        if self.process is not None:
            return
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=self.environment,
            )
        except OSError as exc:
            raise MCPTransportError(
                f"could not start MCP process: {exc}", code="TRANSPORT_START_FAILED", phase="connect", retryable=True
            ) from exc
        self.connected = True
        self._start_reader(self.process.stdout, self._stdout, "stdout")
        self._start_reader(self.process.stderr, self._stderr, "stderr")

    def send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise MCPTransportError("MCP process is not running")
        try:
            process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPTransportError(f"could not write to MCP process: {exc}") from exc

    def receive(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.001, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for MCP response")
            try:
                line = self._stdout.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if self.process is not None and self.process.poll() is not None:
                    raise MCPTransportError(f"MCP process exited with code {self.process.returncode}")
                continue
            if line is None:
                code = self.process.returncode if self.process is not None else None
                raise MCPTransportError(f"MCP stdout closed (process exit code {code})")
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MCPTransportError(f"invalid JSON-RPC stdout: {exc.msg}") from exc
            if not isinstance(message, dict):
                raise MCPTransportError("MCP JSON-RPC message must be an object")
            return message

    def close(self) -> None:
        process = self.process
        if process is None:
            self._stdout_capture.close()
            self._stderr_capture.close()
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._drain(self._stderr)
        self.process = None
        self.connected = False
        self._stdout_capture.close()
        self._stderr_capture.close()

    def finish(self, timeout: float = 2.0) -> int | None:
        """Close stdin after a one-shot call and let the server flush output."""
        process = self.process
        if process is None:
            return None
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return process.poll()
        # Reader threads append to captures before enqueueing their EOF marker.
        deadline = time.monotonic() + min(timeout, 0.5)
        while time.monotonic() < deadline and (self._stdout.empty() or self._stderr.empty()):
            time.sleep(0.005)
        return process.returncode

    @property
    def stdout_text(self) -> str:
        return self._captured_text("stdout")

    @property
    def stderr_text(self) -> str:
        self._drain(self._stderr)
        return self._captured_text("stderr")

    @property
    def returncode(self) -> int | None:
        return self.process.poll() if self.process is not None else None

    def _start_reader(self, stream: Any, destination: queue.Queue[str | None], name: str) -> None:
        def read() -> None:
            if stream is None:
                destination.put(None)
                return
            for line in stream:
                self._capture(name, line)
                if name == "stdout":
                    destination.put(line)
            destination.put(None)

        threading.Thread(target=read, daemon=True).start()

    def _capture(self, name: str, value: str) -> None:
        encoded = value.encode("utf-8", errors="replace")
        if name == "stdout":
            output, lock, count = self._stdout_capture, self._stdout_lock, self._stdout_bytes
        else:
            output, lock, count = self._stderr_capture, self._stderr_lock, self._stderr_bytes
        remaining = self.max_capture_bytes - count
        with lock:
            if remaining > 0:
                output.write(encoded[:remaining])
                output.flush()
        saved = max(0, min(len(encoded), remaining))
        if name == "stdout":
            self._stdout_bytes += saved
        else:
            self._stderr_bytes += saved
        if len(encoded) > remaining:
            self.output_truncated = True

    def _drain(self, source: queue.Queue[str | None]) -> None:
        while True:
            try:
                value = source.get_nowait()
            except queue.Empty:
                return
            # Streams are written by reader threads; draining only clears the queue.

    def _captured_text(self, name: str) -> str:
        output = self._stdout_capture if name == "stdout" else self._stderr_capture
        lock = self._stdout_lock if name == "stdout" else self._stderr_lock
        if output.closed:
            return ""
        with lock:
            position = output.tell()
            output.seek(0)
            raw = output.read()
            output.seek(position)
        return raw.decode("utf-8", errors="replace")


class DockerStdioTransport(StdioTransport):
    """Docker stdio transport whose command is derived only from host config."""

    def __init__(self, server: MCPServerConfig, *, workspace: Path | None = None) -> None:
        command = build_stdio_command(server, workspace=workspace)
        super().__init__(
            command,
            environment=build_subprocess_environment(server.environment, server.stdio.secret_refs if server.stdio else {}),
            max_capture_bytes=min(server.max_output_bytes, server.max_artifact_bytes),
        )


class _ControlledRedirectHandler(HTTPRedirectHandler):
    def __init__(self, endpoint: str, allow_same_origin: bool) -> None:
        self._origin = _origin(endpoint)
        self._allow_same_origin = allow_same_origin

    def redirect_request(self, request: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        if not self._allow_same_origin or _origin(newurl) != self._origin:
            raise MCPTransportError(
                "MCP HTTP redirect was blocked by endpoint policy",
                code="HTTP_REDIRECT_BLOCKED",
                phase="http_request",
            )
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class StreamableHTTPTransport:
    """MCP Streamable HTTP client supporting JSON and multi-event SSE responses."""

    def __init__(self, server: MCPServerConfig) -> None:
        if server.http is None:
            raise MCPTransportError("HTTP transport is missing http configuration", code="CONFIG_ERROR")
        self.config = server.http
        self.timeout = server.timeout_seconds
        self.connected = False
        self.output_truncated = False
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._closed = False
        context = ssl.create_default_context() if self.config.verify_tls else ssl._create_unverified_context()
        proxy = ProxyHandler({"http": self.config.proxy_url, "https": self.config.proxy_url}) if self.config.proxy_url else ProxyHandler({})
        self._opener = build_opener(
            proxy,
            HTTPSHandler(context=context),
            _ControlledRedirectHandler(self.config.url, self.config.allow_same_origin_redirects),
        )

    def connect(self) -> None:
        self.connected = True
        self._closed = False

    def send(self, message: dict[str, Any]) -> None:
        if not self.connected or self._closed:
            raise MCPTransportError("MCP HTTP transport is not connected", code="HTTP_CONNECT_FAILED")
        headers = self._request_headers()
        request = Request(
            self.config.url,
            data=json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                self._capture_response_metadata(response.headers)
                status = int(getattr(response, "status", 200))
                raw = response.read()
                if status == 202 or not raw:
                    return
                self._enqueue_response(raw, response.headers.get("Content-Type", ""))
        except MCPTransportError:
            raise
        except HTTPError as exc:
            if exc.code in {401, 403}:
                code, retryable = "AUTH_ERROR", False
            elif exc.code in {404, 410} and self._session_id:
                self._session_id = None
                code, retryable = "HTTP_SESSION_EXPIRED", True
            elif 500 <= exc.code < 600:
                code, retryable = "HTTP_SERVER_ERROR", True
            else:
                code, retryable = "HTTP_REQUEST_FAILED", False
            raise MCPTransportError(
                f"MCP HTTP request failed with status {exc.code}", code=code, phase="http_request", retryable=retryable
            ) from exc
        except ssl.SSLError as exc:
            raise MCPTransportError(f"MCP TLS validation failed: {exc}", code="TLS_ERROR", phase="tls") from exc
        except (TimeoutError, URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLError):
                code, phase = "TLS_ERROR", "tls"
            else:
                code, phase = "HTTP_CONNECT_FAILED", "http_request"
            raise MCPTransportError(str(reason), code=code, phase=phase, retryable=code != "TLS_ERROR") from exc

    def receive(self, timeout: float) -> dict[str, Any]:
        try:
            message = self._messages.get(timeout=max(0.001, timeout))
        except queue.Empty as exc:
            raise TimeoutError("timed out waiting for MCP HTTP response") from exc
        result = message.get("result")
        if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
            self._protocol_version = result["protocolVersion"]
        return message

    def close(self) -> None:
        if self._closed:
            return
        if self._session_id:
            request = Request(self.config.url, headers=self._request_headers(), method="DELETE")
            try:
                self._opener.open(request, timeout=min(self.timeout, 5)).close()
            except Exception:
                pass
        self._session_id = None
        self.connected = False
        self._closed = True

    def finish(self, timeout: float = 2.0) -> int | None:
        return None

    @property
    def stdout_text(self) -> str:
        return ""

    @property
    def stderr_text(self) -> str:
        return ""

    @property
    def returncode(self) -> int | None:
        return None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def reset_session(self) -> None:
        self._session_id = None
        self._protocol_version = None

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.config.headers,
            **resolve_secret_refs(self.config.secret_refs),
        }
        if self._session_id:
            headers["MCP-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def _capture_response_metadata(self, headers: Any) -> None:
        session_id = headers.get("MCP-Session-Id")
        if session_id:
            self._session_id = str(session_id)

    def _enqueue_response(self, raw: bytes, content_type: str) -> None:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MCPTransportError("MCP HTTP response was not UTF-8", code="MCP_PROTOCOL_ERROR") from exc
        media_type = content_type.partition(";")[0].strip().casefold()
        if media_type == "text/event-stream":
            messages = _parse_sse_messages(text)
        elif media_type in {"application/json", ""} or media_type.endswith("+json"):
            messages = _parse_json_messages(text)
        else:
            raise MCPTransportError(
                f"unsupported MCP HTTP content type: {media_type or 'missing'}", code="MCP_PROTOCOL_ERROR"
            )
        for message in messages:
            self._messages.put(message)


def build_transport(server: MCPServerConfig, *, workspace: Path | None = None) -> MCPTransport:
    if server.transport == "streamable_http":
        return StreamableHTTPTransport(server)
    command = build_stdio_command(server, workspace=workspace)
    cls = DockerStdioTransport if Path(server.command).name.casefold() in {"docker", "docker.exe"} else StdioTransport
    if cls is DockerStdioTransport:
        return DockerStdioTransport(server, workspace=workspace)
    return StdioTransport(
        command,
        environment=build_subprocess_environment(server.environment, server.stdio.secret_refs if server.stdio else {}),
        max_capture_bytes=min(server.max_output_bytes, server.max_artifact_bytes),
    )


def build_stdio_command(server: MCPServerConfig, *, workspace: Path | None = None) -> list[str]:
    if server.transport != "stdio" or server.stdio is None:
        raise MCPTransportError("build_stdio_command requires a stdio server", code="CONFIG_ERROR")
    if workspace is None and any("{workspace}" in item for item in server.args):
        raise MCPTransportError("MCP args use {workspace} but no Solver workspace was supplied")
    args = [item.replace("{workspace}", str(workspace)) if workspace else item for item in server.args]
    is_docker = server.stdio.source == "docker_image"
    command = ["docker", "run", "--rm", "-i", str(server.stdio.image)] if is_docker else [server.command, *args]
    if not is_docker:
        return command
    try:
        run_index = next(index for index, value in enumerate(command) if value == "run")
    except StopIteration as exc:
        raise MCPTransportError("docker MCP command must contain the 'run' subcommand") from exc
    options: list[str] = []
    security = server.docker
    if security is not None:
        if security.memory:
            options.extend(["--memory", security.memory])
        if security.cpus is not None:
            options.extend(["--cpus", str(security.cpus)])
        if security.pids_limit is not None:
            options.extend(["--pids-limit", str(security.pids_limit)])
        if security.network:
            options.extend(["--network", security.network])
        if security.read_only:
            options.append("--read-only")
        if security.cap_drop_all:
            options.extend(["--cap-drop", "ALL"])
        for capability in security.cap_add:
            options.extend(["--cap-add", capability])
        if security.no_new_privileges:
            options.extend(["--security-opt", "no-new-privileges"])
        for mount, mount_options in security.tmpfs.items():
            options.extend(["--tmpfs", f"{mount}:{mount_options}"])
    for variable in sorted({*server.environment, *(server.stdio.secret_refs if server.stdio else {})}):
        options.extend(["--env", variable])
    # A Docker MCP receives the active Solver workspace automatically for real
    # task calls. Catalog discovery has no task workspace and intentionally
    # starts without a mount. The user cannot widen this boundary through MCP
    # configuration: the complete workspace is read-only and only its dedicated
    # artifacts directory is writable.
    if workspace is not None:
        resolved = workspace.resolve()
        if not resolved.is_dir():
            raise MCPTransportError("the Solver workspace does not exist", code="CONFIG_ERROR")
        artifacts = resolved / "artifacts"
        if artifacts.is_symlink():
            raise MCPTransportError("the Solver artifacts directory may not be a symlink", code="CONFIG_ERROR")
        artifacts.mkdir(parents=True, exist_ok=True)
        resolved_artifacts = artifacts.resolve()
        try:
            resolved_artifacts.relative_to(resolved)
        except ValueError as exc:
            raise MCPTransportError("the Solver artifacts directory escapes the workspace", code="CONFIG_ERROR") from exc
        options.extend(
            [
                "--volume",
                f"{resolved}:{AUTOMATIC_WORKSPACE_CONTAINER_PATH}:ro",
                "--volume",
                f"{resolved_artifacts}:{AUTOMATIC_ARTIFACTS_CONTAINER_PATH}:rw",
            ]
        )
    # Put docker flags immediately after `run`, before image/entrypoint arguments.
    return command[: run_index + 1] + _deduplicate_docker_options(command[run_index + 1 :], options) + command[run_index + 1 :]


def build_subprocess_environment(configured: dict[str, str], secret_refs: dict[str, str] | None = None) -> dict[str, str]:
    # Do not inherit backend secrets. Keep only variables required to launch a process.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update(configured)
    environment.update(resolve_secret_refs(secret_refs or {}))
    return environment


def resolve_secret_refs(secret_refs: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for target, reference in secret_refs.items():
        _, variable = reference.split(":", 1)
        value = os.environ.get(variable)
        if value is None:
            raise MCPTransportError(
                f"required secret environment variable is not set: {variable}",
                code="AUTH_ERROR",
                phase="secret_resolution",
            )
        resolved[target] = value
    return resolved


def _deduplicate_docker_options(existing: list[str], options: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    takes_value = {
        "--memory", "--cpus", "--pids-limit", "--network", "--cap-drop", "--cap-add", "--security-opt",
        "--tmpfs", "--volume", "--env",
    }
    while index < len(options):
        option = options[index]
        width = 2 if option in takes_value else 1
        present = option in existing or any(item.startswith(option + "=") for item in existing)
        if not present:
            result.extend(options[index : index + width])
        index += width
    return result


def _parse_json_messages(value: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise MCPTransportError(f"invalid MCP HTTP JSON: {exc.msg}", code="MCP_PROTOCOL_ERROR") from exc
    values = payload if isinstance(payload, list) else [payload]
    if any(not isinstance(item, dict) for item in values):
        raise MCPTransportError("MCP HTTP JSON-RPC messages must be objects", code="MCP_PROTOCOL_ERROR")
    return values


def _parse_sse_messages(value: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n") + [""]:
        if line == "":
            if data_lines:
                messages.extend(_parse_json_messages("\n".join(data_lines)))
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, content = line.partition(":")
        if field == "data":
            data_lines.append(content[1:] if content.startswith(" ") else content)
    return messages


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port
