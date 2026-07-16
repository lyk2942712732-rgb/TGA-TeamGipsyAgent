"""MCP capability helpers: catalog snapshots and health projection."""

from __future__ import annotations

from datetime import UTC, datetime

from tga.tools.mcp_healthcheck import HealthcheckRecord, check_mcp_security_hub
from tga.tools.tool_runner import ToolRunner


def tool_catalog_snapshot(runner: ToolRunner | None) -> dict:
    if runner is None:
        return {"tools": [], "availability": "unavailable", "reason": "tool runner is not configured"}
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


def health_snapshot(runner: ToolRunner | None) -> dict:
    checked_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if runner is None:
        return {"checked_at": checked_at, "records": [{"tool": "mcp", "status": "unavailable", "detail": "tool runner is not configured"}]}
    records: list[HealthcheckRecord] = check_mcp_security_hub(runner.catalog.hub_root)
    return {"checked_at": checked_at, "records": [record.model_dump() for record in records]}
