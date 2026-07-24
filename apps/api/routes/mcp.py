"""Mcp HTTP boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request

from tga.contracts import ExecutionPolicy, LocalComputeExecutionPolicy, MCPCapabilitySnapshot, MCPCapabilityTool, NetworkExecutionPolicy, TGATask
from tga.tools.mcp_config import MCPServerConfig, delete_mcp_server, load_mcp_config, patch_mcp_server, set_mcp_server_enabled, upsert_mcp_server
from tga.tools.mcp_importer import DEFAULT_MAX_PACKAGE_BYTES, MCPImageImporter, MCPImportError
from tga.tools.mcp_manager import MCPManager
from tga.tools.mcp_policy import redact_sensitive

from apps.api.routes.support import MCPEnabledRequest, MCPMethodTestRequest, _catalog_runner, _run_root

router = APIRouter(tags=["mcp"])
_audit_lock = threading.Lock()


@router.post("/tools/mcp/refresh")
def refresh_mcp_catalog() -> dict[str, Any]:
    """Refresh discovery now; active LLM turns retain their prior snapshot."""
    manager = _catalog_runner()
    manager.refresh()
    return manager.status_snapshot()


def _public_server_config(server: MCPServerConfig) -> dict[str, Any]:
    payload = server.model_dump(mode="json", by_alias=True, exclude_none=True)
    http = payload.get("http")
    if isinstance(http, dict) and isinstance(http.get("url"), str) and "?" in http["url"]:
        http["url"] = http["url"].split("?", 1)[0] + "?redacted"
    return payload


def _load_mcp_config_for_api(manager: MCPManager):
    """Load the operator-owned MCP config without leaking parser failures as 500s."""
    try:
        return load_mcp_config(manager.config_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MCP_CONFIG_NOT_FOUND",
                "message": "MCP configuration is unavailable",
                "reason": str(exc),
            },
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MCP_CONFIG_UNREADABLE",
                "message": "MCP configuration could not be read",
                "reason": str(exc),
            },
        ) from exc
    except ValueError as exc:
        reason = str(exc)
        if len(reason) > 4000:
            reason = f"{reason[:4000]}\n... validation output truncated"
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MCP_CONFIG_INVALID",
                "message": "MCP configuration is invalid",
                "reason": reason,
            },
        ) from exc


def _server_record(server_id: str) -> dict[str, Any]:
    manager = _catalog_runner()
    config, _ = _load_mcp_config_for_api(manager)
    server = config.servers.get(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}")
    status = next((item for item in manager.status_snapshot()["records"] if item["server"] == server_id), None)
    return {"id": server_id, "config": _public_server_config(server), "status": status}


@router.get("/mcp/servers")
def list_mcp_servers() -> dict[str, Any]:
    manager = _catalog_runner()
    config, _ = _load_mcp_config_for_api(manager)
    statuses = {item["server"]: item for item in manager.status_snapshot()["records"]}
    return {
        "servers": [
            {"id": server_id, "config": _public_server_config(server), "status": statuses.get(server_id)}
            for server_id, server in sorted(config.servers.items())
        ]
    }


@router.post("/mcp/servers", status_code=201)
def create_mcp_server(payload: dict[str, Any]) -> dict[str, Any]:
    server_id = str(payload.get("id") or "").strip()
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raw_config = {key: value for key, value in payload.items() if key != "id"}
    try:
        server = MCPServerConfig.model_validate(raw_config)
        action = upsert_mcp_server(_catalog_runner().config_path, server_id, server)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _catalog_runner().refresh()
    return {"action": action, "server": _server_record(server_id)}


@router.get("/mcp/servers/{server_id}")
def get_mcp_server(server_id: str) -> dict[str, Any]:
    return _server_record(server_id)


@router.patch("/mcp/servers/{server_id}")
def update_mcp_server(server_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        patch_mcp_server(_catalog_runner().config_path, server_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _catalog_runner().refresh()
    return {"server": _server_record(server_id)}


@router.delete("/mcp/servers/{server_id}")
def delete_managed_mcp_server(server_id: str) -> dict[str, Any]:
    return remove_mcp_server(server_id)


@router.post("/mcp/servers/{server_id}/test")
def test_mcp_server(server_id: str) -> dict[str, Any]:
    manager = _catalog_runner()
    try:
        discovery = manager.test_server(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}") from exc
    return discovery.model_dump(mode="json")


@router.post("/mcp/servers/{server_id}/refresh")
def refresh_one_mcp_server(server_id: str) -> dict[str, Any]:
    manager = _catalog_runner()
    config, _ = _load_mcp_config_for_api(manager)
    if server_id not in config.servers:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}")
    manager.refresh()
    return _server_record(server_id)


@router.get("/mcp/servers/{server_id}/tools")
def list_mcp_server_tools(server_id: str) -> dict[str, Any]:
    manager = _catalog_runner()
    try:
        discovery = manager.test_server(server_id)
        config, _ = _load_mcp_config_for_api(manager)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}") from exc
    enabled = set(config.servers[server_id].enabled_tools)
    return {
        "server_id": server_id,
        "status": discovery.status,
        "protocol_version": discovery.protocol_version,
        "server_info": discovery.server_info,
        "error": discovery.error,
        "tools": [
            {**tool.model_dump(mode="json"), "enabled": not enabled or tool.name in enabled}
            for tool in discovery.tools
        ],
    }


@router.post("/mcp/servers/{server_id}/tools/{tool_name:path}/test")
def test_mcp_method(server_id: str, tool_name: str, payload: MCPMethodTestRequest) -> dict[str, Any]:
    """Execute one real method through the shared production MCP manager."""
    manager = _catalog_runner()
    snapshot = manager.ensure_catalog()
    server = manager.config.servers.get(server_id) if manager.config else None
    if server is None:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}")
    if not server.enabled:
        raise HTTPException(status_code=409, detail=f"MCP server is disabled: {server_id}")
    route = next((item for item in snapshot.routes if item.server_id == server_id and item.method == tool_name), None)
    if route is None:
        # One controlled refresh is allowed before declaring the catalog stale.
        snapshot = manager.refresh()
        route = next((item for item in snapshot.routes if item.server_id == server_id and item.method == tool_name), None)
    if route is None:
        raise HTTPException(status_code=404, detail=f"MCP method is not discovered: {server_id}.{tool_name}")
    risk = manager.policy.risk_for(server=server, method=route.method)
    if risk == "destructive":
        raise HTTPException(status_code=403, detail="destructive MCP method tests are forbidden")
    if risk == "active" and not payload.confirm_active:
        raise HTTPException(status_code=409, detail="active MCP method test requires confirm_active=true")
    method_policy = server.methods.get(route.method)
    allowed_modes = method_policy.modes if method_policy and method_policy.modes is not None else server.visibility.modes
    task = TGATask(
        id="mcp_method_test", name="MCP method test", mode=allowed_modes[0],
        goal="Explicit operator-authorized MCP method test",
        execution_policy=ExecutionPolicy(
            network=NetworkExecutionPolicy(
                access="custom" if server.transport == "streamable_http" else "disabled",
                interaction="interact",
                custom_origins=[server.http.url] if server.http is not None else [],
            ),
            # Explicit operator testing of an active local MCP method still
            # needs a non-host execution boundary under the v5 policy model.
            local_compute=LocalComputeExecutionPolicy(mode="isolated"),
        ),
        session_input={"prompt": "Explicit operator-authorized MCP method test"},
        schema_version=5,
        mcp_capabilities=MCPCapabilitySnapshot(
            catalog_version=snapshot.version,
            server_ids=[server_id],
            tools=[MCPCapabilityTool(**item.model_dump(mode="json")) for item in snapshot.routes if item.server_id == server_id],
        ),
    )
    outcome = manager.call_tool(
        task=task, route=route, arguments=payload.arguments,
        catalog_version=snapshot.version, trace_id=f"trace_method_test_{os.urandom(8).hex()}",
    )
    _append_mcp_method_test_audit(
        server_id=server_id,
        method=tool_name,
        risk=risk,
        confirm_active=payload.confirm_active,
        arguments=payload.arguments,
        outcome=outcome.model_dump(mode="json"),
    )
    preview = redact_sensitive({"content": outcome.content, "structured_content": outcome.structured_content})
    encoded = json.dumps(preview, ensure_ascii=False, default=str)
    return {
        "ok": outcome.ok,
        "server": server_id,
        "method": tool_name,
        "trace_id": outcome.trace_id,
        "request_id": outcome.request_id,
        "catalog_version": outcome.catalog_version,
        "protocol_version": outcome.protocol_version,
        "server_info": outcome.server_info,
        "timings": outcome.timings,
        "is_error": outcome.is_error,
        "error": outcome.error.model_dump(mode="json") if outcome.error else None,
        "content_preview": encoded[:12000],
        "truncated": len(encoded) > 12000 or outcome.artifact_truncated or outcome.output_truncated,
        "explicit_active_authorization": bool(risk == "active" and payload.confirm_active),
    }


def _append_mcp_method_test_audit(
    *, server_id: str, method: str, risk: str, confirm_active: bool,
    arguments: dict[str, Any], outcome: dict[str, Any],
) -> None:
    record = {
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event": "MCP_METHOD_TEST",
        "server": server_id,
        "method": method,
        "risk": risk,
        "explicit_active_authorization": bool(risk == "active" and confirm_active),
        "arguments": redact_sensitive(arguments),
        "ok": outcome.get("ok"),
        "request_id": outcome.get("request_id"),
        "trace_id": outcome.get("trace_id"),
        "timings": outcome.get("timings") or {},
        "error": outcome.get("error"),
    }
    audit_path = _run_root() / "mcp-method-tests.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with _audit_lock:
        with audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


@router.get("/mcp/images")
def list_local_mcp_images() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["docker", "image", "ls", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail="Docker CLI is unavailable") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=503, detail=result.stderr.strip() or "docker image ls failed")
    images = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        repository, tag = item.get("Repository"), item.get("Tag")
        item["name"] = f"{repository}:{tag}" if repository and tag else item.get("ID", "")
        images.append(item)
    return {"images": images}


@router.post("/mcp/images/{image:path}/inspect")
def inspect_local_mcp_image(image: str) -> dict[str, Any]:
    if not image or any(character in image for character in ("\x00", "\r", "\n")):
        raise HTTPException(status_code=400, detail="invalid Docker image name")
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False, shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail="Docker CLI is unavailable") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=404, detail="local Docker image was not found; TGA did not pull it")
    try:
        details = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Docker returned invalid inspect output") from exc
    return {"image": image, "local": True, "details": details[0] if details else {}}


@router.delete("/tools/mcp/{server_id}")
def remove_mcp_server(server_id: str) -> dict[str, Any]:
    """Remove one explicit server entry; the underlying Docker image is retained."""
    manager = _catalog_runner()
    try:
        removed = delete_mcp_server(manager.config_path, server_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}")
    manager.refresh()
    return {
        "deleted": True,
        "server_id": server_id,
        "image_deleted": False,
        "catalog": manager.status_snapshot(),
    }


@router.patch("/tools/mcp/{server_id}/enabled")
def change_mcp_server_enabled(server_id: str, request: MCPEnabledRequest) -> dict[str, Any]:
    """Enable or disable one configured server and refresh dynamic discovery."""
    manager = _catalog_runner()
    try:
        enabled = set_mcp_server_enabled(manager.config_path, server_id, request.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    manager.refresh()
    return {
        "server_id": server_id,
        "enabled": enabled,
        "catalog": manager.status_snapshot(),
    }


@router.post("/tools/mcp/import")
@router.post("/mcp/images/import")
async def import_mcp_package(request: Request) -> dict[str, Any]:
    """Build/load one operator-selected MCP package and add it to mcp.json.

    The endpoint intentionally accepts a raw body rather than multipart data:
    clients cannot submit Docker arguments, tags, mounts or environment values.
    """
    encoded_name = request.headers.get("x-tga-filename", "")
    filename = unquote(encoded_name)
    if not filename or Path(filename).name != filename or "\x00" in filename:
        raise HTTPException(status_code=400, detail="A valid X-TGA-Filename header is required")
    try:
        max_bytes = int(os.environ.get("TGA_MCP_IMPORT_MAX_BYTES", str(DEFAULT_MAX_PACKAGE_BYTES)))
    except ValueError:
        max_bytes = DEFAULT_MAX_PACKAGE_BYTES
    max_bytes = max(1, min(max_bytes, DEFAULT_MAX_PACKAGE_BYTES))
    upload_root = (_run_root() / ".mcp-imports").resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="upload-", suffix=Path(filename).suffix, dir=upload_root)
    received = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > max_bytes:
                    raise HTTPException(status_code=413, detail=f"MCP package exceeds the {max_bytes} byte limit")
                output.write(chunk)
        manager = _catalog_runner()
        importer = MCPImageImporter(config_path=manager.config_path, max_package_bytes=max_bytes)
        try:
            result = await asyncio.to_thread(importer.import_package, temporary_name, filename)
        except MCPImportError as exc:
            status = 503 if exc.code in {"DOCKER_UNAVAILABLE", "DOCKER_TIMEOUT"} else 400
            raise HTTPException(status_code=status, detail=f"{exc.code}: {exc}") from exc
        await asyncio.to_thread(manager.refresh)
        result.catalog = manager.status_snapshot()
        return result.model_dump(mode="json")
    finally:
        try:
            Path(temporary_name).unlink()
        except FileNotFoundError:
            pass
