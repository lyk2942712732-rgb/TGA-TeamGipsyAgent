"""Bounded, task-scoped MCP directory gateway exposed to the model."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tga.contracts import TGATask
from tga.tools.mcp_manager import MCPManager
from tga.tools.mcp_registry import MCPCatalogSnapshot, MCPToolRoute


TGA_MCP_TOOL = "tga_mcp"
MAX_SEARCH_RESULTS = 20
MAX_LIST_RESULTS = 50


def gateway_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TGA_MCP_TOOL,
            "description": "Browse and call only the MCP services explicitly selected for this task.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": ["status", "list", "search", "describe", "call"]},
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "query": {"type": "string", "maxLength": 200},
                    "arguments": {"type": "object"},
                    "_tga": {"type": "object"},
                },
                "required": ["action"],
            },
        },
    }


class MCPGateway:
    def __init__(self, *, manager: MCPManager, task: TGATask, snapshot: MCPCatalogSnapshot) -> None:
        self.manager = manager
        self.task = task
        self.snapshot = snapshot

    def query(self, *, action: str, server: str = "", tool: str = "", query: str = "") -> dict[str, Any]:
        if action == "status":
            status = self.manager.status_snapshot(task=self.task)
            allowed = self.task.mcp_capabilities.server_ids if self.task.schema_version >= 4 else self.task.mcp_servers
            records = [item for item in status["records"] if item.get("server") in allowed]
            return {"action": action, "catalog_version": self.snapshot.version, "servers": records}
        if action == "list":
            routes = self._routes(server=server)[:MAX_LIST_RESULTS]
            return {
                "action": action,
                "catalog_version": self.snapshot.version,
                "count": len(routes),
                "truncated": len(self._routes(server=server)) > MAX_LIST_RESULTS,
                "tools": [self._summary(route) for route in routes],
            }
        if action == "search":
            needle = query.strip().casefold()
            if not needle:
                raise ValueError("query is required for search")
            matches = [
                route for route in self._routes(server=server)
                if needle in route.method.casefold() or needle in route.server_id.casefold() or needle in route.description.casefold()
            ][:MAX_SEARCH_RESULTS]
            return {"action": action, "catalog_version": self.snapshot.version, "count": len(matches), "tools": [self._summary(route) for route in matches]}
        if action == "describe":
            route = self.resolve(server=server, tool=tool)
            return {**self._summary(route), "action": action, "input_schema": self._bounded_schema(route.input_schema), "workspace_warning": self._workspace_warning(route)}
        raise ValueError(f"unsupported MCP catalog action: {action}")

    def resolve(self, *, server: str, tool: str) -> MCPToolRoute:
        if not tool:
            raise ValueError("tool is required")
        matches = [
            route for route in self._routes(server=server)
            if tool in {route.method, route.provider_name}
        ]
        if not matches:
            raise ValueError("MCP method is not visible for this task")
        if len(matches) > 1:
            raise ValueError("MCP method name is ambiguous; provide server")
        return matches[0]

    def _routes(self, *, server: str = "") -> list[MCPToolRoute]:
        return [route for route in self.snapshot.routes if not server or route.server_id == server]

    def _summary(self, route: MCPToolRoute) -> dict[str, Any]:
        config = self.manager.config.servers[route.server_id]
        denial = self.manager.policy.call_denial(task=self.task, server_id=route.server_id, server=config, method=route.method)
        return {
            "server": route.server_id,
            "tool": route.method,
            "provider_name": route.provider_name,
            "description": route.description[:500],
            "risk": self.manager.policy.risk_for(server=config, method=route.method),
            "allowed": denial is None,
            "reason": denial,
        }

    def _workspace_warning(self, route: MCPToolRoute) -> str | None:
        required = set(route.input_schema.get("required") or [])
        server = self.manager.config.servers[route.server_id]
        is_docker = server.transport == "stdio" and server.stdio and server.stdio.source == "docker_image"
        if ("filepath" in required or "path" in required) and is_docker:
            return "Task calls automatically mount the Solver workspace read-only. Materialize the input and pass its /workspace path; write generated files under /workspace/artifacts."
        return None

    @staticmethod
    def _bounded_schema(schema: dict[str, Any], limit: int = 32_000) -> dict[str, Any]:
        encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, default=str)
        if len(encoded.encode("utf-8")) <= limit:
            return schema
        return {
            "truncated": True,
            "reason": "input schema exceeds the MCP gateway response limit",
            "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "original_bytes": len(encoded.encode("utf-8")),
        }
