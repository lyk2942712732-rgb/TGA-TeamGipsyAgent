from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tga.tools.mcp_config import MCPServerConfig
from tga.tools.mcp_manager import MCPManager
from tga.contracts import TGATask
from tests.runtime_fixtures import execution_policy, mcp_snapshot, task as v6_task


class _MCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list[dict[str, Any]] = []
    deleted = False
    use_sse = False

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        message = json.loads(self.rfile.read(length))
        type(self).requests.append({"message": message, "headers": dict(self.headers)})
        method = message.get("method")
        if method == "discover":
            payload = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": "Method not found"},
            }
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            payload: Any = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "test-http", "version": "1"},
                    "capabilities": {},
                },
            }
        elif method == "tools/list":
            payload = {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [
                {"name": "echo", "description": "Echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
                {"name": "hidden", "description": "Not allowlisted", "inputSchema": {"type": "object", "properties": {}}},
            ]}}
        elif method == "tools/call":
            text = message.get("params", {}).get("arguments", {}).get("text", "")
            payload = {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [{"type": "text", "text": text}]}}
        else:
            payload = {"jsonrpc": "2.0", "id": message.get("id"), "result": {}}
        if type(self).use_sse:
            body = (
                'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
                f"event: message\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
            ).encode()
            content_type = "text/event-stream"
        else:
            body = json.dumps(payload, separators=(",", ":")).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("MCP-Session-Id", "session-123")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self) -> None:
        type(self).deleted = True
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture()
def endpoint() -> str:
    _MCPHandler.requests = []
    _MCPHandler.deleted = False
    _MCPHandler.use_sse = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MCPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_transport_config_is_discriminated_and_sensitive_headers_use_refs(monkeypatch: pytest.MonkeyPatch, endpoint: str, tmp_path) -> None:
    with pytest.raises(ValueError):
        MCPServerConfig.model_validate(
            {
                "transport": "streamable_http",
                "stdio": {"source": "docker_image", "image": "demo:latest"},
                "http": {"url": endpoint},
            }
        )
    with pytest.raises(ValueError):
        MCPServerConfig.model_validate(
            {"transport": "streamable_http", "http": {"url": endpoint, "headers": {"Authorization": "secret"}}}
        )
    monkeypatch.delenv("MISSING_MCP_TOKEN", raising=False)
    config = tmp_path / "missing-secret.json"
    config.write_text(
        json.dumps({"version": 1, "servers": {"remote": {
            "transport": "streamable_http",
            "http": {"url": endpoint, "secretRefs": {"Authorization": "env:MISSING_MCP_TOKEN"}},
        }}}),
        encoding="utf-8",
    )
    manager = MCPManager(config_path=config, cache_path=tmp_path / "cache.json")
    discovery = manager.refresh().servers[0]
    assert discovery.error and "MISSING_MCP_TOKEN" in discovery.error["message"]


def test_http_server_discovery_allowlist_and_agent_tool_call(endpoint: str, tmp_path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({
            "version": 1,
            "servers": {
                "remote": {
                    "transport": "streamable_http",
                    "http": {"url": endpoint},
                    "enabledTools": ["echo"],
                }
            },
        }),
        encoding="utf-8",
    )
    manager = MCPManager(config_path=config, cache_path=tmp_path / "cache.json")
    snapshot = manager.refresh()
    assert [route.provider_name for route in snapshot.routes] == ["mcp__remote__echo"]
    task = v6_task(
        id="http_mcp", name="http mcp", mode="ctf", task_entry_url="http://127.0.0.1/", goal="test",
        execution_policy=execution_policy([endpoint]),
        mcp_capabilities=mcp_snapshot(snapshot, "remote"),
    )
    outcome = manager.call_tool(
        context=task,
        route=snapshot.routes[0],
        arguments={"text": "hello over HTTP"},
        catalog_version=snapshot.version,
    )
    assert outcome.ok is True
    assert outcome.content == [{"type": "text", "text": "hello over HTTP"}]
    assert outcome.protocol_version == "2024-11-05"
    assert _MCPHandler.deleted is True
