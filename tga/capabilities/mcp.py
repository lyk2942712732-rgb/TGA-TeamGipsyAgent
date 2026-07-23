"""MCP capability helpers: catalog snapshots and health projection."""

from __future__ import annotations

from datetime import UTC, datetime

from tga.tools.mcp_healthcheck import HealthcheckRecord, check_mcp_security_hub
from tga.tools.mcp_manager import MCPManager
from tga.tools.tool_runner import ToolRunner


def tool_catalog_snapshot(runner: ToolRunner | MCPManager | None) -> dict:
    if runner is None:
        return {"tools": [], "availability": "unavailable", "reason": "tool runner is not configured"}
    if isinstance(runner, MCPManager):
        snapshot = runner.ensure_catalog()
        return {
            "availability": "healthy" if runner.config_error is None else "unavailable",
            "catalog_version": snapshot.version,
            "tools": [
                {
                    "tool_id": route.server_id,
                    "provider_name": route.provider_name,
                    "methods": [{"name": route.method, "description": route.description, "input_schema": route.input_schema}],
                    "risk": runner.policy.risk_for(server=runner.config.servers[route.server_id], method=route.method) if runner.config else "active",
                    "modes": (
                        runner.config.servers[route.server_id].methods[route.method].modes
                        if runner.config
                        and route.method in runner.config.servers[route.server_id].methods
                        and runner.config.servers[route.server_id].methods[route.method].modes is not None
                        else runner.config.servers[route.server_id].visibility.modes if runner.config else []
                    ),
                }
                for route in snapshot.routes
            ],
            "reason": runner.config_error,
        }
    return {
        "availability": "healthy",
        "tools": [
            {
                "tool_id": server.id,
                "methods": [
                    {"name": method.name, "description": method.description, "input_schema": method.input_schema}
                    for method in server.tools
                ],
                "risk": "active",
            }
            for server in runner.catalog.servers
        ],
    }


def health_snapshot(runner: ToolRunner | MCPManager | None) -> dict:
    checked_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if runner is None:
        return {"checked_at": checked_at, "records": [{"tool": "mcp", "status": "unavailable", "detail": "tool runner is not configured"}]}
    if isinstance(runner, MCPManager):
        return {"checked_at": checked_at, **runner.status_snapshot()}
    records: list[HealthcheckRecord] = check_mcp_security_hub(runner.catalog.hub_root)
    return {"checked_at": checked_at, "records": [record.model_dump() for record in records]}
