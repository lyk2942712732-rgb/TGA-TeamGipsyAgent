"""Ephemeral gateway routes for remote MCP tools."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tga.runtime.tooling.requests import ToolClass


class ToolCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_tool_name: str
    capability: str
    tool_class: ToolClass
    backend: Literal["host_control", "host_retrieval", "sandbox", "remote_mcp"]
    description: str
    parameters: dict[str, Any]
    risk: str = "passive"
    max_read_chars: int | None = None
    mcp_server_id: str | None = None
    mcp_method: str | None = None
    execution_profile_id: str | None = None


class RuntimeToolCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[ToolCatalogEntry, ...]

    def get(self, provider_tool_name: str) -> ToolCatalogEntry | None:
        return next(
            (item for item in self.entries if item.provider_tool_name == provider_tool_name),
            None,
        )

    @classmethod
    def from_runtime(cls, *, mcp_snapshot, **_ignored: Any) -> "RuntimeToolCatalog":
        values = []
        for route in tuple(getattr(mcp_snapshot, "routes", ()) or ()):
            values.append(ToolCatalogEntry(
                provider_tool_name=route.provider_name,
                capability=f"mcp:{route.server_id}:{route.method}",
                tool_class="execution",
                backend="remote_mcp",
                description=route.description or f"Call {route.server_id}.{route.method}",
                parameters=json.loads(json.dumps(
                    route.input_schema or {"type": "object", "properties": {}}
                )),
                risk="active",
                mcp_server_id=route.server_id,
                mcp_method=route.method,
            ))
        return cls(entries=tuple(values))


__all__ = ["RuntimeToolCatalog", "ToolCatalogEntry"]
