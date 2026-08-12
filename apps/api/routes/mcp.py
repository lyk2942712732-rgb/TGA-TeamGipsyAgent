"""MCP configuration and discovery using the same tools loaded by Runtime."""

# FastAPI dependencies are intentionally declared in function defaults.
# ruff: noqa: B008

from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import container
from tga2.bootstrap import Container, mcp_server_from_frontend
from tga2.integrations.mcp import MCPConfig, load_mcp_tools

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _get(app: Container, server_id: str):
    try:
        return app.mcp.list()[server_id]
    except KeyError as exc:
        raise HTTPException(404, "MCP server not found") from exc


def _managed(server_id: str, config: dict, app: Container):
    loaded = [
        tool
        for tool in app.runtime.external_tools
        if tool.name.startswith(f"{server_id}_")
    ]
    return {
        "id": server_id,
        "config": config,
        "status": {
            "server": server_id,
            "status": "ready" if loaded else "configured",
            "configured": True,
            "enabled": config.get("enabled", True),
            "reachable": True if loaded else None,
            "discovered": bool(loaded),
            "tools": len(loaded),
            "transport": config.get("transport", "stdio"),
            "error": app.mcp_errors.get(server_id),
        },
    }


def _merge(target: dict, patch: dict) -> dict:
    result = dict(target)
    for key, value in patch.items():
        result[key] = (
            _merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else value
        )
    return result


@router.get("/servers")
def servers(app: Container = Depends(container)):
    return {
        "servers": [
            _managed(name, config, app) for name, config in app.mcp.list().items()
        ]
    }


@router.post("/servers", status_code=201)
async def create(payload: dict, app: Container = Depends(container)):
    server_id = str(payload.get("id") or "")
    config = dict(payload.get("config") or {})
    mcp_server_from_frontend(config)
    action = app.mcp.put(server_id, config)
    await app.refresh_mcp_tools()
    return {"action": action, "server": _managed(server_id, config, app)}


@router.patch("/servers/{server_id}")
async def patch(server_id: str, payload: dict, app: Container = Depends(container)):
    try:
        config = _merge(_get(app, server_id), payload)
        mcp_server_from_frontend(config)
        app.mcp.put(server_id, config)
        await app.refresh_mcp_tools()
    except KeyError as exc:
        raise HTTPException(404, "MCP server not found") from exc
    return {"server": _managed(server_id, config, app)}


@router.delete("/servers/{server_id}")
async def delete(server_id: str, app: Container = Depends(container)):
    deleted = app.mcp.delete(server_id)
    await app.refresh_mcp_tools()
    return {"deleted": deleted, "server_id": server_id, "image_deleted": False}


@router.post("/servers/{server_id}/refresh")
async def refresh(server_id: str, app: Container = Depends(container)):
    config = _get(app, server_id)
    await app.refresh_mcp_tools()
    return _managed(server_id, config, app)


@router.get("/servers/{server_id}/tools")
async def tools(server_id: str, app: Container = Depends(container)):
    config = _get(app, server_id)
    try:
        values = await load_mcp_tools(
            MCPConfig(servers={server_id: mcp_server_from_frontend(config)})
        )
    except Exception as exc:  # noqa: BLE001 - remote tool errors are response data
        return {
            "server_id": server_id,
            "status": "unavailable",
            "tools": [],
            "error": {"code": type(exc).__name__, "message": str(exc)},
        }
    return {
        "server_id": server_id,
        "status": "discovered",
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.args_schema.model_json_schema()
                if hasattr(tool.args_schema, "model_json_schema")
                else tool.args_schema,
                "enabled": True,
            }
            for tool in values
        ],
    }


@router.post("/servers/{server_id}/tools/{tool_name:path}/test")
async def test(
    server_id: str, tool_name: str, payload: dict, app: Container = Depends(container)
):
    if not payload.get("confirm_active"):
        raise HTTPException(409, "MCP test calls require confirm_active=true")
    started = perf_counter()
    discovered = await tools(server_id, app)
    if discovered.get("status") != "ready":
        return {"ok": False, "error": discovered.get("error")}
    values = await load_mcp_tools(
        MCPConfig(servers={server_id: mcp_server_from_frontend(_get(app, server_id))})
    )
    tool = next(
        (
            item
            for item in values
            if item.name == tool_name or item.name.endswith(f"_{tool_name}")
        ),
        None,
    )
    if tool is None:
        raise HTTPException(404, "MCP tool not found")
    try:
        result = await tool.ainvoke(payload.get("arguments") or {})
        return {
            "ok": True,
            "trace_id": uuid4().hex,
            "request_id": uuid4().hex,
            "timings": {"total_ms": round((perf_counter() - started) * 1000, 2)},
            "content_preview": str(result)[:4000],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - remote tool errors are response data
        return {
            "ok": False,
            "trace_id": uuid4().hex,
            "request_id": uuid4().hex,
            "timings": {"total_ms": round((perf_counter() - started) * 1000, 2)},
            "content_preview": "",
            "error": {"code": type(exc).__name__, "message": str(exc)},
        }
