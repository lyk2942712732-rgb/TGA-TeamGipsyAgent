"""TGA-owned MCP endpoint policy; transport implementation belongs to the adapter."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPServer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: Literal["stdio", "streamable_http", "sse", "websocket"]
    command: str | None = None
    args: list[str] = Field(default_factory=list, max_length=128)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    env: dict[str, str] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def validate_transport(self) -> MCPServer:
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server requires command")
        if self.transport != "stdio":
            parsed = urlsplit(self.url or "")
            if (
                parsed.scheme not in {"http", "https", "ws", "wss"}
                or not parsed.hostname
            ):
                raise ValueError("remote MCP server requires a valid URL")
            if parsed.username or parsed.password:
                raise ValueError("credentials must not be embedded in MCP URLs")
        return self

    def adapter_payload(self) -> dict[str, Any]:
        if self.transport == "stdio":
            payload: dict[str, Any] = {
                "transport": "stdio",
                "command": self.command,
                "args": self.args,
            }
            if self.env:
                payload["env"] = self.env
            return payload
        payload = {"transport": self.transport, "url": self.url}
        if self.headers:
            payload["headers"] = self.headers
        return payload


class MCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servers: dict[str, MCPServer] = Field(default_factory=dict, max_length=64)

    def adapter_connections(self) -> dict[str, dict[str, Any]]:
        return {name: server.adapter_payload() for name, server in self.servers.items()}


class MCPConfigRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def list(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if not self.path.is_file():
                return {}
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return dict(payload.get("servers") or {})

    def put(self, server_id: str, config: dict[str, Any]) -> str:
        if not server_id or not server_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("invalid MCP server id")
        with self._lock:
            values = self.list()
            action = "updated" if server_id in values else "created"
            values[server_id] = config
            self._save(values)
            return action

    def patch(self, server_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            values = self.list()
            if server_id not in values:
                raise KeyError(server_id)
            values[server_id] = _merge(values[server_id], patch)
            self._save(values)
            return values[server_id]

    def delete(self, server_id: str) -> bool:
        with self._lock:
            values = self.list()
            removed = values.pop(server_id, None) is not None
            if removed:
                self._save(values)
            return removed

    def _save(self, servers: dict[str, dict[str, Any]]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"servers": servers}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


async def load_mcp_tools(config: MCPConfig) -> list[BaseTool]:
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("MCP support requires langchain-mcp-adapters") from exc
    client = MultiServerMCPClient(
        config.adapter_connections(), tool_name_prefix=True, handle_tool_errors=True
    )
    tools = await client.get_tools()
    for tool in tools:
        tool.metadata = {
            **(tool.metadata or {}),
            "tga2_source": "mcp",
            "tga2_risk": "active",
        }
    return tools


def _merge(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(target)
    for key, value in patch.items():
        result[key] = (
            _merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else value
        )
    return result


__all__ = ["MCPConfig", "MCPConfigRepository", "MCPServer", "load_mcp_tools"]
