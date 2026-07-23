from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tga.contracts import ExecutionPolicy, MCPCapabilitySnapshot, MCPCapabilityTool, TGATask
from tga.tools.mcp_config import load_mcp_config
from tga.tools.mcp_manager import MCPManager
from tga.tools.mcp_policy import validate_json_schema
from tga.tools.mcp_registry import provider_tool_name
from tga.tools.mcp_registry import MCPToolRoute
from tga.tools.mcp_registry import MCPDiscoveredTool, MCPServerDiscovery, build_catalog_snapshot
from tga.tools.mcp_transport import build_stdio_command, build_transport


def _config(tmp_path: Path, *, max_inline: int = 32000, max_artifact: int = 1024 * 1024) -> Path:
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "servers": {
                    "fixture": {
                        "command": sys.executable,
                        "args": [str(fixture)],
                        "maxInlineChars": max_inline,
                        "maxArtifactBytes": max_artifact,
                        "visibility": {"risk": "passive"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _task(server: str = "fixture") -> TGATask:
    return TGATask(
        id="task_dynamic",
        name="dynamic",
        mode="ctf",
        target="http://127.0.0.1",
        goal="test",
        allow_active_scan=False,
        mcp_servers=[server],
    )


def _schema_v4_task(snapshot, *, task_id: str = "task_v4") -> TGATask:
    return TGATask(
        id=task_id,
        name=task_id,
        mode="incident_response",
        goal="test MCP lifecycle",
        mode_config={"mode": "incident_response"},
        execution_policy=ExecutionPolicy(),
        session_input={"hint": {"text": "inspect"}},
        mcp_capabilities=MCPCapabilitySnapshot(
            catalog_version=snapshot.version,
            server_ids=sorted({item.server_id for item in snapshot.servers if item.status == "discovered"}),
            tools=[MCPCapabilityTool(**route.model_dump(mode="json")) for route in snapshot.routes],
        ),
        schema_version=4,
    )


def test_config_rejects_duplicate_servers_and_invalid_name(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"version":1,"servers":{"a":{"command":"x"},"a":{"command":"y"}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_mcp_config(duplicate)
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"version":1,"servers":{"bad name":{"command":"x"}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid MCP server name"):
        load_mcp_config(invalid)


def test_legacy_mcp_modes_are_normalized_and_filter_current_tasks(tmp_path: Path) -> None:
    path = _config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["servers"]["fixture"]["visibility"]["modes"] = ["web_audit", "binary_ctf"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    config, _ = load_mcp_config(path)
    assert config.servers["fixture"].visibility.modes == ["penetration_test", "reverse_engineering"]
    manager = MCPManager(config_path=path, cache_path=tmp_path / "cache.json")
    manager.refresh()
    allowed = _task().model_copy(update={"mode": "penetration_test"})
    denied = _task().model_copy(update={"mode": "incident_response"})
    assert manager.snapshot_for_task(allowed).route("mcp__fixture__echo") is not None
    assert manager.snapshot_for_task(denied).route("mcp__fixture__echo") is None


def test_dynamic_initialize_list_and_call(tmp_path: Path) -> None:
    manager = MCPManager(config_path=_config(tmp_path), cache_path=tmp_path / "cache.json")
    discovered = manager.refresh()
    assert [route.provider_name for route in discovered.routes] == [
        "mcp__fixture__echo",
        "mcp__fixture__large_result",
    ]
    snapshot = manager.snapshot_for_task(_task())
    route = snapshot.route("mcp__fixture__echo")
    assert route is not None
    outcome = manager.call_tool(
        task=_task(), route=route, arguments={"text": "done"}, catalog_version=snapshot.version
    )
    assert outcome.ok
    assert outcome.content == [{"type": "text", "text": "done"}]
    assert "fixture diagnostic" in outcome.stderr
    assert "fixture diagnostic" not in outcome.stdout


def test_ensure_catalog_reloads_when_mcp_config_changes(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text('{"version":1,"servers":{}}', encoding="utf-8")
    manager = MCPManager(config_path=path, cache_path=tmp_path / "cache.json")
    assert manager.ensure_catalog().routes == ()

    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    path.write_text(
        json.dumps({"version": 1, "servers": {"fixture": {"command": sys.executable, "args": [str(fixture)]}}}),
        encoding="utf-8",
    )

    assert {route.method for route in manager.ensure_catalog().routes} == {"echo", "large_result"}


def test_schema_v4_existing_session_is_denied_immediately_after_global_disable(tmp_path: Path) -> None:
    config_path = _config(tmp_path)
    manager = MCPManager(config_path=config_path, cache_path=tmp_path / "cache.json")
    created_catalog = manager.refresh()
    task = _schema_v4_task(created_catalog)
    route = manager.snapshot_for_task(task).route("mcp__fixture__echo")
    assert route is not None
    assert manager.call_tool(
        task=task, route=route, arguments={"text": "before"}, catalog_version=created_catalog.version,
    ).ok

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["servers"]["fixture"]["enabled"] = False
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    denied = manager.call_tool(
        task=task, route=route, arguments={"text": "after"}, catalog_version=created_catalog.version,
    )

    assert denied.ok is False
    assert denied.error and denied.error.code == "CONFIG_ERROR"
    assert manager.snapshot_for_task(task).routes == ()


def test_schema_v4_new_service_is_visible_only_to_sessions_created_after_refresh(tmp_path: Path) -> None:
    config_path = _config(tmp_path)
    manager = MCPManager(config_path=config_path, cache_path=tmp_path / "cache.json")
    first_catalog = manager.refresh()
    existing_task = _schema_v4_task(first_catalog, task_id="existing")

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["servers"]["later"] = payload["servers"]["fixture"]
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    current_catalog = manager.ensure_catalog()
    new_task = _schema_v4_task(current_catalog, task_id="new")

    existing_servers = {route.server_id for route in manager.snapshot_for_task(existing_task).routes}
    new_servers = {route.server_id for route in manager.snapshot_for_task(new_task).routes}
    assert existing_servers == {"fixture"}
    assert new_servers == {"fixture", "later"}


def test_invalid_arguments_do_not_start_call(tmp_path: Path) -> None:
    manager = MCPManager(config_path=_config(tmp_path), cache_path=tmp_path / "cache.json")
    snapshot = manager.refresh()
    route = snapshot.route("mcp__fixture__echo")
    assert route is not None
    outcome = manager.call_tool(task=_task(), route=route, arguments={}, catalog_version=snapshot.version)
    assert not outcome.ok
    assert outcome.error and outcome.error.code == "INVALID_ARGUMENTS"
    assert outcome.timings == {}


def test_forged_undiscovered_method_is_rejected(tmp_path: Path) -> None:
    manager = MCPManager(config_path=_config(tmp_path), cache_path=tmp_path / "cache.json")
    snapshot = manager.refresh()
    route = MCPToolRoute(provider_name="mcp__fixture__not_real", server_id="fixture", method="not_real", input_schema={"type": "object"})
    outcome = manager.call_tool(task=_task("fail"), route=route, arguments={}, catalog_version=snapshot.version)
    assert not outcome.ok
    assert outcome.error and outcome.error.code == "TOOL_NOT_VISIBLE"
    assert outcome.timings == {}


def test_one_discovery_failure_does_not_hide_healthy_server(tmp_path: Path) -> None:
    path = _config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["servers"]["broken"] = {"command": str(tmp_path / "definitely-missing"), "visibility": {"risk": "passive"}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    manager = MCPManager(config_path=path, cache_path=tmp_path / "cache.json")
    snapshot = manager.refresh()
    assert snapshot.route("mcp__fixture__echo") is not None
    broken = next(item for item in snapshot.servers if item.server_id == "broken")
    assert broken.error and broken.error["code"] == "DISCOVERY_ERROR"


def test_valid_config_hash_cache_is_used_without_rediscovery(tmp_path: Path, monkeypatch) -> None:
    config_path = _config(tmp_path)
    cache_path = tmp_path / "cache.json"
    first = MCPManager(config_path=config_path, cache_path=cache_path)
    expected = first.refresh()
    second = MCPManager(config_path=config_path, cache_path=cache_path)
    monkeypatch.setattr(second, "_discover", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cache miss")))
    cached = second.ensure_catalog()
    assert [route.provider_name for route in cached.routes] == [route.provider_name for route in expected.routes]


def test_method_policy_adds_host_side_argument_constraints(tmp_path: Path) -> None:
    path = _config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["servers"]["fixture"]["methods"] = {
        "echo": {"risk": "passive", "argumentSchema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 3}}}}
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    manager = MCPManager(config_path=path, cache_path=tmp_path / "cache.json")
    snapshot = manager.refresh()
    route = snapshot.route("mcp__fixture__echo")
    assert route is not None
    outcome = manager.call_tool(task=_task(), route=route, arguments={"text": "too long"}, catalog_version=snapshot.version)
    assert not outcome.ok
    assert outcome.error and outcome.error.code == "INVALID_ARGUMENTS"


def test_server_rate_limit_is_enforced(tmp_path: Path) -> None:
    path = _config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["servers"]["fixture"].update({"callsPerMinute": 0.1, "burst": 1})
    path.write_text(json.dumps(payload), encoding="utf-8")
    manager = MCPManager(config_path=path, cache_path=tmp_path / "cache.json")
    snapshot = manager.refresh()
    route = snapshot.route("mcp__fixture__echo")
    assert route is not None
    first = manager.call_tool(task=_task(), route=route, arguments={"text": "one"}, catalog_version=snapshot.version)
    second = manager.call_tool(task=_task(), route=route, arguments={"text": "two"}, catalog_version=snapshot.version)
    assert first.ok
    assert not second.ok
    assert second.error and second.error.code == "POLICY_DENIED" and second.error.phase == "rate_limit"


def test_provider_name_is_stable_legal_and_bounded() -> None:
    value = provider_tool_name("server.with spaces", "method/" + "x" * 100)
    assert value == provider_tool_name("server.with spaces", "method/" + "x" * 100)
    assert len(value) <= 64
    assert all(character.isalnum() or character in "_-" for character in value)


def test_provider_name_collision_gets_stable_hash_suffix() -> None:
    discovery = MCPServerDiscovery(
        server_id="fixture",
        config_hash="hash",
        discovered_at="2026-07-21T00:00:00Z",
        tools=(MCPDiscoveredTool(name="same.name"), MCPDiscoveredTool(name="same/name")),
    )
    first = build_catalog_snapshot([discovery])
    second = build_catalog_snapshot([discovery])
    names = [route.provider_name for route in first.routes]
    assert len(set(names)) == 2
    assert names == [route.provider_name for route in second.routes]


def test_schema_validator_resolves_local_refs_and_composition() -> None:
    schema = {
        "type": "object",
        "$defs": {"positive": {"type": "integer", "exclusiveMinimum": 0}},
        "properties": {"count": {"$ref": "#/$defs/positive"}, "mode": {"enum": ["safe"]}},
        "required": ["count"],
        "allOf": [{"not": {"required": ["forbidden"]}}],
        "additionalProperties": False,
    }
    assert validate_json_schema(schema, {"count": 2, "mode": "safe"}) is None
    assert "must be > 0" in str(validate_json_schema(schema, {"count": 0}))
    assert "forbidden" in str(validate_json_schema(schema, {"count": 2, "forbidden": True}))


def test_docker_command_includes_resource_controls(tmp_path: Path) -> None:
    path = tmp_path / "docker.json"
    path.write_text(
        json.dumps({"version": 1, "servers": {"safe": {"command": "docker", "args": ["run", "--rm", "-i", "image:latest"]}}}),
        encoding="utf-8",
    )
    config, _ = load_mcp_config(path)
    command = build_stdio_command(config.servers["safe"])
    for option in ["--memory", "--cpus", "--pids-limit", "--network", "--read-only", "--cap-drop", "--security-opt"]:
        assert option in command
    assert command[-1] == "image:latest"


def test_docker_task_call_automatically_mounts_workspace_with_writable_artifacts(tmp_path: Path) -> None:
    path = tmp_path / "docker.json"
    path.write_text(
        json.dumps({"version": 1, "servers": {"safe": {"command": "docker", "args": ["run", "--rm", "-i", "image:latest"]}}}),
        encoding="utf-8",
    )
    workspace = tmp_path / "solver"
    workspace.mkdir()
    config, _ = load_mcp_config(path)

    discovery_command = build_stdio_command(config.servers["safe"])
    task_command = build_stdio_command(config.servers["safe"], workspace=workspace)

    assert "--volume" not in discovery_command
    assert f"{workspace.resolve()}:/workspace:ro" in task_command
    assert f"{(workspace / 'artifacts').resolve()}:/workspace/artifacts:rw" in task_command
    assert (workspace / "artifacts").is_dir()


def test_workspace_status_distinguishes_local_docker_and_remote_http(tmp_path: Path) -> None:
    path = tmp_path / "workspace-status.json"
    path.write_text(json.dumps({
        "version": 1,
        "servers": {
            "docker": {"command": "docker", "args": ["run", "--rm", "-i", "image:latest"], "enabled": False},
            "remote": {
                "enabled": False,
                "transport": "streamable_http",
                "http": {"url": "https://mcp.example.test/mcp"},
            },
        },
    }), encoding="utf-8")
    manager = MCPManager(config_path=path, cache_path=tmp_path / "cache.json")
    records = {item["server"]: item for item in manager.status_snapshot()["records"]}

    assert records["docker"]["workspace_access"] == {
        "mode": "automatic",
        "mounted_on_task_call": True,
        "container_path": "/workspace",
        "read_only": True,
        "artifacts_path": "/workspace/artifacts",
        "artifacts_writable": True,
    }
    assert records["remote"]["workspace_access"] == {"mode": "remote", "mounted_on_task_call": False}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("timeout", "TIMEOUT"), ("invalid-json", "MCP_PROTOCOL_ERROR"), ("rpc-error", "MCP_INITIALIZE_FAILED"), ("exit", "PROCESS_EXITED")],
)
def test_protocol_and_process_failures_are_classified(tmp_path: Path, mode: str, expected: str) -> None:
    fixture = Path(__file__).parent / "fixtures" / "failing_mcp_server.py"
    path = tmp_path / "fail.json"
    path.write_text(
        json.dumps({"version": 1, "servers": {"fail": {"command": sys.executable, "args": [str(fixture), mode], "timeoutSeconds": 1, "toolTimeoutSeconds": 1, "visibility": {"risk": "passive"}}}}),
        encoding="utf-8",
    )
    manager = MCPManager(config_path=path, cache_path=tmp_path / "cache.json")
    # Seed the referenced immutable catalog to exercise call-time failures;
    # discovery itself intentionally cannot succeed for this fixture.
    route = MCPToolRoute(provider_name="mcp__fail__demo", server_id="fail", method="demo", input_schema={"type": "object"})
    from tga.tools.mcp_registry import MCPCatalogSnapshot
    snapshot = MCPCatalogSnapshot(version=f"failure_{mode}", routes=(route,))
    manager.config, _ = load_mcp_config(path)
    manager.snapshot = snapshot
    manager._catalog_versions[snapshot.version] = snapshot
    outcome = manager.call_tool(task=_task("fail"), route=route, arguments={}, catalog_version=snapshot.version)
    assert not outcome.ok
    assert outcome.error and outcome.error.code == expected
    assert outcome.error.phase in {"initialize", "transport_start"}


def test_transport_close_leaves_no_child_process(tmp_path: Path) -> None:
    path = tmp_path / "cleanup.json"
    path.write_text(
        json.dumps({"version": 1, "servers": {"sleep": {"command": sys.executable, "args": ["-c", "import time; time.sleep(30)"], "visibility": {"risk": "passive"}}}}),
        encoding="utf-8",
    )
    config, _ = load_mcp_config(path)
    transport = build_transport(config.servers["sleep"])
    transport.connect()
    process = transport.process
    assert process is not None and process.poll() is None
    transport.close()
    assert process.poll() is not None
