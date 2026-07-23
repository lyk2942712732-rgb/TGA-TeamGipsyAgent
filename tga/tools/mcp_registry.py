"""Dynamic MCP catalog and provider-tool name routing."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


PROVIDER_NAME_LIMIT = 64


class MCPDiscoveredTool(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class MCPServerDiscovery(BaseModel):
    model_config = ConfigDict(frozen=True)

    server_id: str
    config_hash: str
    server_info: dict[str, Any] = Field(default_factory=dict)
    protocol_version: str = ""
    tools: tuple[MCPDiscoveredTool, ...] = ()
    discovered_at: str
    status: str = "discovered"
    error: dict[str, Any] | None = None


class MCPToolRoute(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_name: str
    server_id: str
    method: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPCatalogSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    routes: tuple[MCPToolRoute, ...] = ()
    servers: tuple[MCPServerDiscovery, ...] = ()

    def route(self, provider_name: str) -> MCPToolRoute | None:
        return next((item for item in self.routes if item.provider_name == provider_name), None)

    def function_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": route.provider_name,
                    "description": route.description or f"Call {route.server_id} MCP method {route.method}",
                    "parameters": json.loads(json.dumps(route.input_schema or {"type": "object", "properties": {}})),
                },
            }
            for route in self.routes
        ]


def build_catalog_snapshot(discoveries: list[MCPServerDiscovery]) -> MCPCatalogSnapshot:
    used: dict[str, tuple[str, str]] = {}
    routes: list[MCPToolRoute] = []
    for server in sorted(discoveries, key=lambda item: item.server_id):
        if server.status != "discovered":
            continue
        for tool in sorted(server.tools, key=lambda item: item.name):
            provider = provider_tool_name(server.server_id, tool.name)
            identity = (server.server_id, tool.name)
            if provider in used and used[provider] == identity:
                continue
            if provider in used and used[provider] != identity:
                suffix = hashlib.sha256(f"{server.server_id}\0{tool.name}".encode()).hexdigest()[:10]
                provider = f"{provider[: PROVIDER_NAME_LIMIT - 11]}_{suffix}"
            if provider in used and used[provider] != identity:
                raise ValueError(f"MCP provider name collision: {identity} and {used[provider]}")
            used[provider] = identity
            routes.append(
                MCPToolRoute(
                    provider_name=provider,
                    server_id=server.server_id,
                    method=tool.name,
                    description=tool.description,
                    input_schema=normalize_input_schema(tool.input_schema),
                )
            )
    digest = hashlib.sha256(
        json.dumps(
            [item.model_dump(mode="json") for item in discoveries],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    return MCPCatalogSnapshot(version=f"mcp_{digest}", routes=tuple(routes), servers=tuple(discoveries))


def provider_tool_name(server_id: str, method: str) -> str:
    raw = f"mcp__{server_id}__{method}"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    if len(safe) <= PROVIDER_NAME_LIMIT:
        return safe
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{safe[: PROVIDER_NAME_LIMIT - 11]}_{suffix}"


def normalize_input_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    value = json.loads(json.dumps(schema or {}))
    if not isinstance(value, dict):
        return {"type": "object", "properties": {}}
    value.setdefault("type", "object")
    value.setdefault("properties", {})
    return value
