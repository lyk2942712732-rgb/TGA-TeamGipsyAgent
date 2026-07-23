from __future__ import annotations

import json
import sys
from pathlib import Path

from tga.contracts import TGATask
from tga.tools.mcp_gateway import MCPGateway, TGA_MCP_TOOL, gateway_definition
from tga.tools.mcp_config import MCPServerConfig
from tga.tools.mcp_manager import MCPManager


def _manager(tmp_path: Path) -> MCPManager:
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({
        "version": 1,
        "servers": {"fixture": {
            "command": sys.executable,
            "args": [str(fixture)],
            "visibility": {"risk": "active"},
            "methods": {"echo": {"risk": "passive"}},
        }},
    }), encoding="utf-8")
    manager = MCPManager(config_path=config, cache_path=tmp_path / "cache.json")
    manager.refresh()
    return manager


def _task(**values) -> TGATask:
    return TGATask(id="gateway", name="gateway", mode="ctf", target="http://127.0.0.1", goal="test", **values)


def test_empty_task_has_no_mcp_and_gateway_is_single_bounded_tool(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert manager.snapshot_for_task(_task()).routes == ()
    definition = gateway_definition()
    assert definition["function"]["name"] == TGA_MCP_TOOL
    assert definition["function"]["parameters"]["additionalProperties"] is False


def test_gateway_search_hides_schema_and_active_method_remains_describable(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    task = _task(mcp_servers=["fixture"], allow_active_scan=False)
    gateway = MCPGateway(manager=manager, task=task, snapshot=manager.snapshot_for_task(task))
    status = gateway.query(action="status")
    assert [item["server"] for item in status["servers"]] == ["fixture"]
    searched = gateway.query(action="search", query="result")
    assert searched["count"] == 1
    assert "input_schema" not in searched["tools"][0]
    described = gateway.query(action="describe", server="fixture", tool="large_result")
    assert described["risk"] == "active"
    assert described["allowed"] is False
    assert "allow_active_scan" in described["reason"]
    passive = gateway.query(action="describe", server="fixture", tool="echo")
    assert passive["allowed"] is True
    active_route = gateway.resolve(server="fixture", tool="large_result")
    denied = manager.call_tool(task=task, route=active_route, arguments={"chars": 1}, catalog_version=gateway.snapshot.version)
    assert denied.error and denied.error.code == "POLICY_DENIED"


def test_health_changes_only_after_real_tools_call(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    task = _task(mcp_servers=["fixture"])
    assert manager.status_snapshot()["records"][0]["runnable"] is None
    route = manager.snapshot_for_task(task).route("mcp__fixture__echo")
    assert route is not None
    invalid = manager.call_tool(task=task, route=route, arguments={}, catalog_version=manager.snapshot.version)
    assert invalid.error and invalid.error.code == "INVALID_ARGUMENTS"
    assert manager.status_snapshot()["records"][0]["runnable"] is None
    outcome = manager.call_tool(task=task, route=route, arguments={"text": "ok"}, catalog_version=manager.snapshot.version)
    health = manager.status_snapshot()["records"][0]
    assert outcome.ok and health["runnable"] is True
    assert health["last_call_method"] == "echo"
    assert health["last_call_at"]


def test_container_rejects_host_windows_path_in_favor_of_automatic_container_path(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    server = MCPServerConfig.model_validate({
        "transport": "stdio", "command": "docker", "args": ["run", "--rm", "-i", "demo:latest"],
        "visibility": {"risk": "passive"}, "workspaceMount": {"enabled": False},
    })
    task = _task(mcp_servers=["fixture"])
    route = manager.snapshot.route("mcp__fixture__echo")
    assert route is not None
    denial = manager.policy.authorize(task=task, server=server, route=route, arguments={"text": r"C:\Users\lyk\sample.bin"})
    assert denial and "/workspace" in denial
