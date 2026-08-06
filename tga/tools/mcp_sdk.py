"""Official MCP SDK integration for configured TGA servers."""

from __future__ import annotations

import os
import re
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urljoin, urlsplit

import anyio
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from tga.tools.mcp_config import MCPServerConfig


AUTOMATIC_WORKSPACE_CONTAINER_PATH = "/workspace"
AUTOMATIC_ARTIFACTS_CONTAINER_PATH = "/workspace/artifacts"


class MCPClientConfigurationError(RuntimeError):
    """Raised before the official SDK opens a connection."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "CONFIG_ERROR",
        phase: str = "configuration",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class MCPConnectionInfo:
    server_info: dict[str, Any]
    protocol_version: str
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class MCPDiscoveryResult(MCPConnectionInfo):
    tools: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class MCPToolResult(MCPConnectionInfo):
    result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MCPResourceResult(MCPConnectionInfo):
    result: dict[str, Any] | None = None


def discover_server(
    server: MCPServerConfig,
    *,
    workspace: Path | None = None,
) -> MCPDiscoveryResult:
    return anyio.run(_discover_server, server, workspace)


def call_tool(
    server: MCPServerConfig,
    *,
    name: str,
    arguments: dict[str, Any],
    workspace: Path | None = None,
) -> MCPToolResult:
    return anyio.run(_call_tool, server, name, arguments, workspace)


def read_resource(
    server: MCPServerConfig,
    *,
    uri: str,
    workspace: Path | None = None,
) -> MCPResourceResult:
    return anyio.run(_read_resource, server, uri, workspace)


async def _discover_server(
    server: MCPServerConfig,
    workspace: Path | None,
) -> MCPDiscoveryResult:
    with anyio.fail_after(server.timeout_seconds):
        async with _connected_client(server, workspace=workspace) as connection:
            tools: list[dict[str, Any]] = []
            cursor: str | None = None
            for _ in range(100):
                page = await connection.client.list_tools(cursor=cursor, cache_mode="reload")
                tools.extend(
                    item.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for item in page.tools
                )
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
            else:
                raise RuntimeError("MCP tools/list exceeded 100 pages")
            info = _connection_info(connection.client)
        return MCPDiscoveryResult(
            server_info=info.server_info,
            protocol_version=info.protocol_version,
            stderr=connection.stderr_text(),
            tools=tuple(tools),
        )


async def _call_tool(
    server: MCPServerConfig,
    name: str,
    arguments: dict[str, Any],
    workspace: Path | None,
) -> MCPToolResult:
    with anyio.fail_after(server.tool_timeout_seconds):
        async with _connected_client(server, workspace=workspace) as connection:
            result = await connection.client.call_tool(
                name,
                arguments,
                read_timeout_seconds=server.tool_timeout_seconds,
            )
            payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            info = _connection_info(connection.client)
        return MCPToolResult(
            server_info=info.server_info,
            protocol_version=info.protocol_version,
            stderr=connection.stderr_text(),
            result=payload,
        )


async def _read_resource(
    server: MCPServerConfig,
    uri: str,
    workspace: Path | None,
) -> MCPResourceResult:
    with anyio.fail_after(server.tool_timeout_seconds):
        async with _connected_client(server, workspace=workspace) as connection:
            result = await connection.client.read_resource(uri, cache_mode="reload")
            payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            info = _connection_info(connection.client)
        return MCPResourceResult(
            server_info=info.server_info,
            protocol_version=info.protocol_version,
            stderr=connection.stderr_text(),
            result=payload,
        )


@dataclass(slots=True)
class _ConnectedClient:
    client: Client
    stderr_file: Any | None
    stderr_output: str = ""

    def stderr_text(self) -> str:
        return self.stderr_output


@asynccontextmanager
async def _connected_client(
    server: MCPServerConfig,
    *,
    workspace: Path | None,
) -> AsyncIterator[_ConnectedClient]:
    _validate_runtime_boundary(server)
    stderr_file = None
    connection = None
    try:
        async with AsyncExitStack() as stack:
            if server.transport == "stdio":
                command = build_stdio_command(server, workspace=workspace)
                stderr_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
                parameters = StdioServerParameters(
                    command=command[0],
                    args=command[1:],
                    env=build_subprocess_environment(
                        server.stdio.environment,
                        server.stdio.secret_refs,
                    ),
                    encoding="utf-8",
                    encoding_error_handler="replace",
                )
                transport = stdio_client(parameters, errlog=stderr_file)
            else:
                if server.http is None:
                    raise MCPClientConfigurationError(
                        "streamable_http transport is missing http configuration"
                    )
                headers = {**server.http.headers, **resolve_secret_refs(server.http.secret_refs)}
                event_hooks = None
                if server.http.allow_same_origin_redirects:
                    event_hooks = {"response": [_same_origin_redirect_guard(server.http.url)]}
                http_client = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        headers=headers,
                        verify=server.http.verify_tls,
                        proxy=server.http.proxy_url,
                        timeout=httpx2.Timeout(
                            server.timeout_seconds,
                            read=server.tool_timeout_seconds,
                        ),
                        follow_redirects=server.http.allow_same_origin_redirects,
                        trust_env=False,
                        event_hooks=event_hooks,
                    )
                )
                transport = streamable_http_client(
                    server.http.url,
                    http_client=http_client,
                    terminate_on_close=True,
                )
            client = Client(
                transport,
                read_timeout_seconds=server.tool_timeout_seconds,
                client_info=Implementation(name="tga", version="0.1.0"),
            )
            connected = await stack.enter_async_context(client)
            connection = _ConnectedClient(client=connected, stderr_file=stderr_file)
            yield connection
    finally:
        if stderr_file is not None and not stderr_file.closed:
            stderr_file.flush()
            stderr_file.seek(0)
            if connection is not None:
                connection.stderr_output = stderr_file.read()
            stderr_file.close()


def _connection_info(client: Client) -> MCPConnectionInfo:
    server_info = (
        client.server_info.model_dump(mode="json", by_alias=True, exclude_none=True)
        if client.server_info is not None
        else {}
    )
    return MCPConnectionInfo(
        server_info=server_info,
        protocol_version=str(client.protocol_version or ""),
    )


def build_stdio_command(
    server: MCPServerConfig,
    *,
    workspace: Path | None = None,
) -> list[str]:
    if server.transport != "stdio" or server.stdio is None:
        raise MCPClientConfigurationError(
            "build_stdio_command requires a stdio server"
        )
    stdio = server.stdio
    if workspace is None and any("{workspace}" in item for item in stdio.args):
        raise MCPClientConfigurationError(
            "MCP args use {workspace} but no session workspace was supplied"
        )
    args = [
        item.replace("{workspace}", str(workspace)) if workspace else item
        for item in stdio.args
    ]
    if stdio.source == "local_process":
        return [str(stdio.command), *args]

    command = ["docker", "run", "--rm", "-i", str(stdio.image)]
    options: list[str] = []
    security = stdio.docker
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
    for variable in sorted({*stdio.environment, *stdio.secret_refs}):
        options.extend(["--env", variable])
    if workspace is not None:
        resolved = workspace.resolve()
        if not resolved.is_dir():
            raise MCPClientConfigurationError("the session workspace does not exist")
        artifacts = resolved / "artifacts"
        if artifacts.is_symlink():
            raise MCPClientConfigurationError(
                "the session artifacts directory may not be a symlink"
            )
        artifacts.mkdir(parents=True, exist_ok=True)
        resolved_artifacts = artifacts.resolve()
        try:
            resolved_artifacts.relative_to(resolved)
        except ValueError as exc:
            raise MCPClientConfigurationError(
                "the session artifacts directory escapes the workspace"
            ) from exc
        options.extend(
            [
                "--volume",
                f"{resolved}:{AUTOMATIC_WORKSPACE_CONTAINER_PATH}:ro",
                "--volume",
                f"{resolved_artifacts}:{AUTOMATIC_ARTIFACTS_CONTAINER_PATH}:rw",
            ]
        )
    return command[:2] + options + command[2:]


def build_subprocess_environment(
    configured: dict[str, str],
    secret_refs: dict[str, str] | None = None,
) -> dict[str, str]:
    return {**configured, **resolve_secret_refs(secret_refs or {})}


def resolve_secret_refs(secret_refs: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for target, reference in secret_refs.items():
        _, variable = reference.split(":", 1)
        value = os.environ.get(variable)
        if value is None:
            raise MCPClientConfigurationError(
                f"required secret environment variable is not set: {variable}",
                code="AUTH_ERROR",
                phase="secret_resolution",
            )
        resolved[target] = value
    return resolved


def _validate_runtime_boundary(server: MCPServerConfig) -> None:
    if os.environ.get("TGA_SANDBOX_RUNTIME") != "enforced" or server.transport != "stdio":
        return
    if server.execution_profile_id is None:
        raise MCPClientConfigurationError(
            "enforced mode requires executionProfileId for local MCP",
            code="POLICY_DENIED",
        )
    if server.stdio is not None and server.stdio.source == "local_process":
        raise MCPClientConfigurationError(
            "local_process MCP is forbidden in enforced mode",
            code="POLICY_DENIED",
        )
    if server.stdio is None or not re.search(
        r"@sha256:[a-f0-9]{64}$", server.stdio.image or ""
    ):
        raise MCPClientConfigurationError(
            "enforced mode requires a digest-pinned MCP image",
            code="POLICY_DENIED",
        )
    raise MCPClientConfigurationError(
        "in-sandbox MCP server images are not authorized; use an external Streamable HTTP service",
        code="POLICY_DENIED",
    )


def _same_origin_redirect_guard(endpoint: str):
    origin = _origin(endpoint)

    async def guard(response: httpx2.Response) -> None:
        location = response.headers.get("location")
        if response.is_redirect and location:
            target = urljoin(str(response.request.url), location)
            if _origin(target) != origin:
                raise httpx2.HTTPError(
                    "MCP HTTP redirect crossed the configured origin",
                    request=response.request,
                )

    return guard


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port


__all__ = [
    "MCPClientConfigurationError",
    "MCPDiscoveryResult",
    "MCPResourceResult",
    "MCPToolResult",
    "build_stdio_command",
    "call_tool",
    "discover_server",
    "read_resource",
]
