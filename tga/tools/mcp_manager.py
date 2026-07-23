"""MCP configuration, discovery, lifecycle, caching and tool execution."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from tga.contracts import TGATask
from tga.tools.mcp_config import DEFAULT_CACHE_PATH, MCPConfig, MCPServerConfig, configured_mcp_path, load_mcp_config
from tga.tools.mcp_policy import MCPPolicy
from tga.tools.mcp_registry import (
    MCPCatalogSnapshot,
    MCPDiscoveredTool,
    MCPServerDiscovery,
    MCPToolRoute,
    build_catalog_snapshot,
)
from tga.tools.mcp_transport import MCPTransport, MCPTransportError, StreamableHTTPTransport, build_transport
from tga.tools.rate_limit import TokenBucket


ERROR_CODES = {
    "CONFIG_ERROR",
    "DISCOVERY_ERROR",
    "TOOL_NOT_VISIBLE",
    "INVALID_ARGUMENTS",
    "POLICY_DENIED",
    "TRANSPORT_START_FAILED",
    "MCP_INITIALIZE_FAILED",
    "MCP_TOOL_ERROR",
    "MCP_PROTOCOL_ERROR",
    "TIMEOUT",
    "PROCESS_EXITED",
    "OUTPUT_TRUNCATED",
    "ARTIFACT_WRITE_FAILED",
    "HTTP_CONNECT_FAILED",
    "HTTP_REQUEST_FAILED",
    "HTTP_SERVER_ERROR",
    "HTTP_SESSION_EXPIRED",
    "HTTP_REDIRECT_BLOCKED",
    "TLS_ERROR",
    "AUTH_ERROR",
}


class MCPExecutionError(BaseModel):
    code: str
    message: str
    phase: str
    retryable: bool = False
    server: str
    method: str | None = None
    trace_id: str


class MCPCallOutcome(BaseModel):
    ok: bool
    server: str
    method: str
    trace_id: str
    request_id: str
    catalog_version: str
    content: list[dict[str, Any]] = Field(default_factory=list)
    structured_content: Any = None
    is_error: bool = False
    raw_result: dict[str, Any] | None = None
    raw_result_json: str = ""
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    timed_out: bool = False
    output_truncated: bool = False
    artifact_truncated: bool = False
    original_bytes: int = 0
    saved_bytes: int = 0
    server_info: dict[str, Any] = Field(default_factory=dict)
    protocol_version: str = ""
    timings: dict[str, int] = Field(default_factory=dict)
    error: MCPExecutionError | None = None


class MCPManager:
    """Owns immutable catalog versions and one-shot MCP connections."""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        cache_path: str | Path | None = None,
        policy: MCPPolicy | None = None,
    ) -> None:
        self.config_path = Path(config_path).expanduser().resolve() if config_path else configured_mcp_path()
        self.cache_path = Path(cache_path or DEFAULT_CACHE_PATH).expanduser().resolve()
        self.policy = policy or MCPPolicy()
        self.config: MCPConfig | None = None
        self.snapshot = MCPCatalogSnapshot(version="mcp_empty")
        self.config_error: str | None = None
        self._loaded = False
        self._config_signature: tuple[int, int] | None = None
        self._lock = threading.RLock()
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._call_health: dict[str, dict[str, Any]] = {}
        self._catalog_versions: dict[str, MCPCatalogSnapshot] = {self.snapshot.version: self.snapshot}
        self._rate_limits: dict[str, TokenBucket] = {}
        self._global_semaphore = threading.BoundedSemaphore(4)

    def ensure_catalog(self, *, workspace: Path | None = None) -> MCPCatalogSnapshot:
        with self._lock:
            signature = _config_file_signature(self.config_path)
            if self._loaded and signature == self._config_signature:
                return self.snapshot
            if self._loaded:
                return self.refresh(workspace=workspace)
            try:
                self.config, self.config_path = load_mcp_config(self.config_path)
                self.config_error = None
            except (OSError, ValueError) as exc:
                self.config_error = str(exc)
                self._config_signature = signature
                self._loaded = True
                return self.snapshot
            cached = self._load_cache(self.config)
            enabled = {key for key, value in self.config.servers.items() if value.enabled}
            if enabled and enabled.issubset({item.server_id for item in cached if item.status == "discovered"}):
                self.snapshot = build_catalog_snapshot(cached)
                self._remember_snapshot(self.snapshot)
                self._reset_semaphores()
                self._config_signature = signature
                self._loaded = True
                return self.snapshot
        return self.refresh(workspace=workspace)

    def refresh(self, *, workspace: Path | None = None) -> MCPCatalogSnapshot:
        with self._lock:
            try:
                config, resolved = load_mcp_config(self.config_path)
            except (OSError, ValueError) as exc:
                self.config_error = str(exc)
                self._config_signature = _config_file_signature(self.config_path)
                self._loaded = True
                self.snapshot = MCPCatalogSnapshot(version="mcp_config_error")
                return self.snapshot
            discoveries: list[MCPServerDiscovery] = []
            config_hash = config.config_hash()
            for server_id, server in config.servers.items():
                if not server.enabled:
                    discoveries.append(
                        MCPServerDiscovery(
                            server_id=server_id,
                            config_hash=config_hash,
                            discovered_at=_utc_now(),
                            status="configured",
                        )
                    )
                    continue
                # Discovery is task-agnostic and must never expose one task's
                # workspace to every enabled server. Mounting is reserved for
                # an authorized resources/read or tools/call below.
                discoveries.append(self._discover(server_id, server, config_hash=config_hash, workspace=None))
            self.config = config
            self.config_path = resolved
            self._config_signature = _config_file_signature(resolved)
            self.config_error = None
            self.snapshot = build_catalog_snapshot(discoveries)
            self._remember_snapshot(self.snapshot)
            self._reset_semaphores()
            self._loaded = True
            self._write_cache(discoveries)
            return self.snapshot

    def snapshot_for_task(self, task: TGATask, *, workspace: Path | None = None) -> MCPCatalogSnapshot:
        snapshot = self.ensure_catalog(workspace=workspace)
        if self.config is None:
            return snapshot
        return self.policy.filter_snapshot(task=task, snapshot=snapshot, servers=self.config.servers)

    def call_tool(
        self,
        *,
        task: TGATask,
        route: MCPToolRoute,
        arguments: dict[str, Any],
        catalog_version: str,
        workspace: Path | None = None,
        trace_id: str | None = None,
    ) -> MCPCallOutcome:
        trace_id = trace_id or f"trace_{uuid4().hex}"
        request_id = f"mcp_{uuid4().hex[:16]}"
        # Cheap config-signature check; if the file changed, perform exactly
        # one controlled catalog refresh before re-authorizing the call.
        self.ensure_catalog(workspace=workspace)
        catalog = self._catalog_versions.get(catalog_version)
        registered_route = catalog.route(route.provider_name) if catalog else None
        if registered_route is None or registered_route.server_id != route.server_id or registered_route.method != route.method:
            return self._failure(route, trace_id, request_id, catalog_version, "TOOL_NOT_VISIBLE", "method was not present in the referenced discovered catalog", "routing")
        server = self.config.servers.get(route.server_id) if self.config else None
        if server is None or not server.enabled:
            return self._failure(route, trace_id, request_id, catalog_version, "CONFIG_ERROR", "route server is not configured and enabled", "config")
        try:
            policy_error = self.policy.authorize(task=task, server=server, route=route, arguments=arguments)
        except Exception as exc:
            return self._failure(route, trace_id, request_id, catalog_version, "INVALID_ARGUMENTS", f"schema validation failed safely: {exc}", "policy")
        if policy_error:
            code = "INVALID_ARGUMENTS" if policy_error.startswith("arguments") else "POLICY_DENIED"
            return self._failure(route, trace_id, request_id, catalog_version, code, policy_error, "policy")

        with self._lock:
            bucket = self._rate_limits.setdefault(
                route.server_id,
                TokenBucket(rate_per_second=server.calls_per_minute / 60.0, burst=server.burst),
            )
            rate_allowed = bucket.allow()
        if not rate_allowed:
            return self._failure(route, trace_id, request_id, catalog_version, "POLICY_DENIED", "MCP server rate limit exceeded", "rate_limit", retryable=True)

        global_semaphore = self._global_semaphore
        global_acquired = global_semaphore.acquire(timeout=server.tool_timeout_seconds)
        if not global_acquired:
            return self._failure(route, trace_id, request_id, catalog_version, "TIMEOUT", "global MCP concurrency slot timed out", "queue", retryable=True)
        semaphore = self._semaphores.setdefault(route.server_id, threading.BoundedSemaphore(server.max_concurrency))
        acquired = semaphore.acquire(timeout=server.tool_timeout_seconds)
        if not acquired:
            global_semaphore.release()
            return self._failure(route, trace_id, request_id, catalog_version, "TIMEOUT", "server concurrency slot timed out", "queue", retryable=True)
        transport: MCPTransport | None = None
        started = time.perf_counter()
        timings: dict[str, int] = {}
        timings["discovery_ms"] = 0  # The immutable catalog was discovered before this call.
        phase = "transport_start"
        try:
            transport = build_transport(server, workspace=workspace)
            phase_start = time.perf_counter()
            transport.connect()
            timings["container_start_ms"] = _elapsed_ms(phase_start)
            initialize_id = f"init_{uuid4().hex[:12]}"
            phase = "initialize"
            phase_start = time.perf_counter()
            initialize = self._initialize(transport, initialize_id, server.timeout_seconds)
            timings["initialize_ms"] = _elapsed_ms(phase_start)
            transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            phase = "tools/call"
            phase_start = time.perf_counter()
            try:
                result = self._rpc(
                    transport,
                    request_id=request_id,
                    method="tools/call",
                    params={"name": route.method, "arguments": arguments},
                    timeout=server.tool_timeout_seconds,
                )
            except MCPTransportError as exc:
                if exc.code != "HTTP_SESSION_EXPIRED" or not isinstance(transport, StreamableHTTPTransport):
                    raise
                # One bounded reinitialization is allowed after the server expires a session.
                transport.reset_session()
                initialize = self._initialize(transport, f"reinit_{uuid4().hex[:12]}", server.timeout_seconds)
                transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                result = self._rpc(
                    transport,
                    request_id=request_id,
                    method="tools/call",
                    params={"name": route.method, "arguments": arguments},
                    timeout=server.tool_timeout_seconds,
                )
            timings["tool_call_ms"] = _elapsed_ms(phase_start)
            transport.finish()
            phase_start = time.perf_counter()
            raw_json = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
            bounded, truncated, original_bytes, saved_bytes = _bounded_utf8(raw_json, server.max_artifact_bytes)
            content = result.get("content") if isinstance(result.get("content"), list) else []
            is_error = bool(result.get("isError") or result.get("is_error"))
            timings["result_serialization_ms"] = _elapsed_ms(phase_start)
            timings["total_ms"] = _elapsed_ms(started)
            error = None
            if is_error:
                error = MCPExecutionError(
                    code="MCP_TOOL_ERROR",
                    message=_content_message(content) or "MCP tool returned isError=true",
                    phase="tools/call",
                    retryable=False,
                    server=route.server_id,
                    method=route.method,
                    trace_id=trace_id,
                )
            elif truncated or transport.output_truncated:
                error = MCPExecutionError(
                    code="OUTPUT_TRUNCATED",
                    message=(
                        f"MCP output exceeded a configured persistence limit; saved {saved_bytes} of {original_bytes} result bytes"
                        if truncated
                        else "MCP stdout or stderr exceeded maxArtifactBytes"
                    ),
                    phase="result_serialization",
                    retryable=False,
                    server=route.server_id,
                    method=route.method,
                    trace_id=trace_id,
                )
            outcome = MCPCallOutcome(
                ok=not is_error,
                server=route.server_id,
                method=route.method,
                trace_id=trace_id,
                request_id=request_id,
                catalog_version=catalog_version,
                content=[item for item in content if isinstance(item, dict)],
                structured_content=result.get("structuredContent", result.get("structured_content")),
                is_error=is_error,
                raw_result=result if not truncated else None,
                raw_result_json=bounded,
                stdout=transport.stdout_text,
                stderr=transport.stderr_text,
                returncode=transport.returncode,
                output_truncated=transport.output_truncated,
                artifact_truncated=truncated,
                original_bytes=original_bytes,
                saved_bytes=saved_bytes,
                server_info=initialize.get("serverInfo") or {},
                protocol_version=str(initialize.get("protocolVersion") or ""),
                timings=timings,
                error=error,
            )
            self._record_call(route=route, outcome=outcome)
            return outcome
        except TimeoutError as exc:
            timings["total_ms"] = _elapsed_ms(started)
            if transport is not None:
                try:
                    transport.send(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/cancelled",
                            "params": {"requestId": request_id, "reason": "TGA hard timeout"},
                        }
                    )
                except MCPTransportError:
                    pass
            return self._failure(route, trace_id, request_id, catalog_version, "TIMEOUT", str(exc), phase, retryable=True, timings=timings, transport=transport)
        except MCPTransportError as exc:
            timings["total_ms"] = _elapsed_ms(started)
            if exc.code in ERROR_CODES:
                code = exc.code
            elif "exited" in str(exc) or "stdout closed" in str(exc):
                code = "PROCESS_EXITED"
            elif phase == "transport_start":
                code = "TRANSPORT_START_FAILED"
            else:
                code = "MCP_PROTOCOL_ERROR"
            return self._failure(route, trace_id, request_id, catalog_version, code, str(exc), phase, retryable=True, timings=timings, transport=transport)
        except Exception as exc:
            timings["total_ms"] = _elapsed_ms(started)
            code = "MCP_INITIALIZE_FAILED" if phase == "initialize" else "MCP_PROTOCOL_ERROR"
            return self._failure(route, trace_id, request_id, catalog_version, code, str(exc), phase, timings=timings, transport=transport)
        finally:
            if transport is not None:
                transport.close()
            semaphore.release()
            global_semaphore.release()

    def read_resource(
        self, *, task: TGATask, server_id: str, uri: str,
        workspace: Path | None = None,
    ) -> dict[str, Any]:
        """Perform a side-effect-free MCP resources/read behind task policy."""

        self.ensure_catalog(workspace=workspace)
        policy = task.execution_policy.mcp if task.execution_policy and task.schema_version < 4 else None
        if task.schema_version >= 4:
            if server_id not in task.mcp_capabilities.server_ids:
                raise PermissionError("MCP_SERVER_NOT_IN_SESSION_SNAPSHOT")
        elif server_id not in task.mcp_servers or (policy and server_id not in policy.enabled_servers):
            raise PermissionError("MCP_SERVER_NOT_AUTHORIZED")
        if policy and policy.enabled_resources and uri not in policy.enabled_resources:
            raise PermissionError("MCP_RESOURCE_NOT_AUTHORIZED")
        server = self.config.servers.get(server_id) if self.config else None
        if server is None or not server.enabled:
            raise PermissionError("MCP_SERVER_NOT_AVAILABLE")
        transport: MCPTransport | None = None
        try:
            transport = build_transport(server, workspace=workspace)
            transport.connect()
            initialize = self._initialize(transport, f"init_{uuid4().hex[:12]}", server.timeout_seconds)
            transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            result = self._rpc(
                transport,
                request_id=f"resource_{uuid4().hex[:16]}",
                method="resources/read",
                params={"uri": uri},
                timeout=server.tool_timeout_seconds,
            )
            transport.finish()
            return {
                "server_id": server_id,
                "resource_uri": uri,
                "contents": result.get("contents") if isinstance(result.get("contents"), list) else [],
                "server_info": initialize.get("serverInfo") or {},
                "protocol_version": str(initialize.get("protocolVersion") or ""),
            }
        finally:
            if transport is not None:
                transport.close()

    def status_snapshot(self, task: TGATask | None = None) -> dict[str, Any]:
        self.ensure_catalog()
        configured = self.config.servers if self.config else {}
        by_id = {item.server_id: item for item in self.snapshot.servers}
        records = []
        for server_id, server in configured.items():
            discovery = by_id.get(server_id)
            call_health = self._call_health.get(server_id) or {}
            visible_count = None
            if task is not None:
                visible_count = sum(1 for route in self.policy.filter_snapshot(task=task, snapshot=self.snapshot, servers=configured).routes if route.server_id == server_id)
            records.append(
                {
                    "server": server_id,
                    "configured": True,
                    "enabled": server.enabled,
                    "reachable": bool(discovery and discovery.status in {"reachable", "discovered"}),
                    "discovered": bool(discovery and discovery.status == "discovered"),
                    "visible_for_task": visible_count,
                    "runnable": call_health.get("runnable"),
                    "last_call_at": call_health.get("last_call_at"),
                    "last_call_method": call_health.get("last_call_method"),
                    "last_call_duration_ms": call_health.get("last_call_duration_ms"),
                    "last_call_error": call_health.get("last_call_error"),
                    "tools": len(discovery.tools) if discovery else 0,
                    "transport": server.transport,
                    "protocol_version": discovery.protocol_version if discovery else "",
                    "server_info": discovery.server_info if discovery else {},
                    "discovered_at": discovery.discovered_at if discovery else None,
                    "image": server.stdio.image if server.stdio and server.stdio.source == "docker_image" else None,
                    "endpoint": _redacted_endpoint(server.http.url) if server.http else None,
                    "workspace_access": _workspace_access(server),
                    "error": discovery.error if discovery else None,
                }
            )
        return {
            "configured": self.config is not None and self.config_error is None,
            "checked_at": _utc_now(),
            "config_path": str(self.config_path),
            "config_error": self.config_error,
            "catalog_version": self.snapshot.version,
            "records": records,
        }

    def _record_call(self, *, route: MCPToolRoute, outcome: MCPCallOutcome) -> None:
        self._call_health[route.server_id] = {
            "runnable": outcome.ok,
            "last_call_at": _utc_now(),
            "last_call_method": route.method,
            "last_call_duration_ms": outcome.timings.get("total_ms"),
            "last_call_error": outcome.error.model_dump(mode="json") if outcome.error else None,
        }

    def test_server(self, server_id: str, *, workspace: Path | None = None) -> MCPServerDiscovery:
        """Discover one configured server without changing its enabled state or catalog snapshot."""
        config, _ = load_mcp_config(self.config_path)
        server = config.servers.get(server_id)
        if server is None:
            raise KeyError(server_id)
        candidate = server.model_copy(update={"enabled": True, "enabled_tools": []})
        return self._discover(server_id, candidate, config_hash=config.config_hash(), workspace=None)

    def close(self) -> None:
        # First release uses one-shot transports. This method is the stable
        # lifecycle hook for future session-reused connections.
        return None

    def _discover(
        self, server_id: str, server: MCPServerConfig, *, config_hash: str, workspace: Path | None
    ) -> MCPServerDiscovery:
        transport: MCPTransport | None = None
        try:
            transport = build_transport(server, workspace=workspace)
            transport.connect()
            initialize = self._initialize(transport, f"init_{uuid4().hex[:12]}", server.timeout_seconds)
            transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            raw_tools = self._list_tools(transport, timeout=server.timeout_seconds)
            enabled_tools = set(server.enabled_tools)
            tools = tuple(
                MCPDiscoveredTool(
                    name=str(item["name"]),
                    description=str(item.get("description") or ""),
                    input_schema=item.get("inputSchema") or item.get("input_schema") or {},
                )
                for item in raw_tools
                if isinstance(item, dict)
                and item.get("name")
                and (not enabled_tools or str(item["name"]) in enabled_tools)
            )
            return MCPServerDiscovery(
                server_id=server_id,
                config_hash=config_hash,
                server_info=initialize.get("serverInfo") or {},
                protocol_version=str(initialize.get("protocolVersion") or ""),
                tools=tools,
                discovered_at=_utc_now(),
            )
        except Exception as exc:
            return MCPServerDiscovery(
                server_id=server_id,
                config_hash=config_hash,
                discovered_at=_utc_now(),
                status="reachable" if transport is not None and transport.connected else "configured",
                error={"code": "DISCOVERY_ERROR", "message": str(exc)[:1000], "phase": "discovery", "retryable": True},
            )
        finally:
            if transport is not None:
                transport.close()

    def _initialize(self, transport: MCPTransport, request_id: str, timeout: int) -> dict[str, Any]:
        return self._rpc(
            transport,
            request_id=request_id,
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "tga", "version": "0.1.0"},
            },
            timeout=timeout,
        )

    def _list_tools(self, transport: MCPTransport, *, timeout: int) -> list[Any]:
        tools: list[Any] = []
        cursor: str | None = None
        for _ in range(100):
            params = {"cursor": cursor} if cursor else {}
            listed = self._rpc(
                transport,
                request_id=f"list_{uuid4().hex[:12]}",
                method="tools/list",
                params=params,
                timeout=timeout,
            )
            tools.extend(listed.get("tools") or [])
            next_cursor = listed.get("nextCursor") or listed.get("next_cursor")
            if not next_cursor:
                return tools
            cursor = str(next_cursor)
        raise RuntimeError("MCP tools/list exceeded 100 pages")

    @staticmethod
    def _rpc(
        transport: MCPTransport, *, request_id: str, method: str, params: dict[str, Any], timeout: int
    ) -> dict[str, Any]:
        transport.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            message = transport.receive(max(0.001, deadline - time.monotonic()))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message.get("error") or {}
                raise RuntimeError(f"MCP {method} error {error.get('code')}: {error.get('message')}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"MCP {method} response did not contain an object result")
            return result

    def _load_cache(self, config: MCPConfig) -> list[MCPServerDiscovery]:
        payload: dict[str, Any] = {}
        candidates = [self.cache_path]
        default_cache = Path(DEFAULT_CACHE_PATH).resolve()
        if self.cache_path.resolve() != default_cache:
            candidates.append(default_cache)
        for candidate in candidates:
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("version") == 1:
                break
        if payload.get("version") != 1:
            return []
        config_hash = config.config_hash()
        values = []
        for server_id, item in (payload.get("servers") or {}).items():
            if not isinstance(item, dict) or item.get("configHash") != config_hash:
                continue
            try:
                values.append(
                    MCPServerDiscovery(
                        server_id=server_id,
                        config_hash=config_hash,
                        server_info=item.get("serverInfo") or {},
                        protocol_version=str(item.get("protocolVersion") or ""),
                        tools=tuple(MCPDiscoveredTool.model_validate(tool) for tool in item.get("tools") or []),
                        discovered_at=str(item.get("discoveredAt") or ""),
                    )
                )
            except ValueError:
                continue
        return values

    def _write_cache(self, discoveries: list[MCPServerDiscovery]) -> None:
        payload = {
            "version": 1,
            "servers": {
                item.server_id: {
                    "configHash": item.config_hash,
                    "serverInfo": item.server_info,
                    "protocolVersion": item.protocol_version,
                    "tools": [tool.model_dump(mode="json") for tool in item.tools],
                    "discoveredAt": item.discovered_at,
                }
                for item in discoveries
                if item.status == "discovered"
            },
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.cache_path)

    def _reset_semaphores(self) -> None:
        self._semaphores = {
            server_id: threading.BoundedSemaphore(server.max_concurrency)
            for server_id, server in (self.config.servers.items() if self.config else [])
        }
        self._rate_limits = {
            server_id: TokenBucket(rate_per_second=server.calls_per_minute / 60.0, burst=server.burst)
            for server_id, server in (self.config.servers.items() if self.config else [])
        }
        self._global_semaphore = threading.BoundedSemaphore(self.config.max_concurrency if self.config else 4)

    def _remember_snapshot(self, snapshot: MCPCatalogSnapshot) -> None:
        self._catalog_versions[snapshot.version] = snapshot

    def _failure(
        self,
        route: MCPToolRoute,
        trace_id: str,
        request_id: str,
        catalog_version: str,
        code: str,
        message: str,
        phase: str,
        *,
        retryable: bool = False,
        timings: dict[str, int] | None = None,
        transport: MCPTransport | None = None,
    ) -> MCPCallOutcome:
        outcome = MCPCallOutcome(
            ok=False,
            server=route.server_id,
            method=route.method,
            trace_id=trace_id,
            request_id=request_id,
            catalog_version=catalog_version,
            stdout=transport.stdout_text if transport else "",
            stderr=transport.stderr_text if transport else "",
            returncode=transport.returncode if transport else None,
            timed_out=code == "TIMEOUT",
            output_truncated=transport.output_truncated if transport else False,
            timings=timings or {},
            error=MCPExecutionError(
                code=code if code in ERROR_CODES else "MCP_PROTOCOL_ERROR",
                message=message[:2000],
                phase=phase,
                retryable=retryable,
                server=route.server_id,
                method=route.method,
                trace_id=trace_id,
            ),
        )
        # Discovery, validation and queue failures do not prove whether the
        # method is runnable.  Only an attempted tools/call changes health.
        if phase == "tools/call":
            self._record_call(route=route, outcome=outcome)
        return outcome


def _bounded_utf8(value: str, limit: int) -> tuple[str, bool, int, int]:
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return value, False, len(raw), len(raw)
    saved = raw[:limit]
    return saved.decode("utf-8", errors="replace"), True, len(raw), len(saved)


def _content_message(content: list[Any]) -> str:
    return "\n".join(str(item.get("text")) for item in content if isinstance(item, dict) and item.get("type") == "text")[:2000]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _config_file_signature(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.stat()
    except OSError:
        return None
    return metadata.st_mtime_ns, metadata.st_size


def _redacted_endpoint(value: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "redacted" if parsed.query else "", ""))


def _workspace_access(server: MCPServerConfig) -> dict[str, Any]:
    if server.transport == "stdio" and server.stdio and server.stdio.source == "docker_image":
        return {
            "mode": "automatic",
            "mounted_on_task_call": True,
            "container_path": "/workspace",
            "read_only": True,
            "artifacts_path": "/workspace/artifacts",
            "artifacts_writable": True,
        }
    if server.transport == "streamable_http":
        return {"mode": "remote", "mounted_on_task_call": False}
    return {"mode": "host_process", "mounted_on_task_call": False}


def config_fingerprint(server_id: str, server: MCPServerConfig) -> str:
    value = json.dumps([server_id, server.model_dump(mode="json", by_alias=True)], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()
