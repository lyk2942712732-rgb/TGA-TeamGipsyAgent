import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.routes import capabilities as capability_routes
from apps.api.routes import events as event_routes
from apps.api.routes import llm_settings as llm_routes
from apps.api.routes import mcp as mcp_routes
from apps.api.routes import sessions as session_routes
from apps.api.routes import support as route_support
from tga.runtime.coordinator import SessionTransitionError
from apps.api.routes import tasks as task_routes
from tga.contracts import ActionSpec, ExecutionPolicy, SessionRecord, TGATask
from tga.evidence.store import EvidenceStore
from tga.tools.mcp_manager import MCPManager
from tga.runtime.task_creation import build_mcp_capability_snapshot
from tga.models.capability_probe import ProviderCapabilityProbe
from tests.runtime_fixtures import configure_verified_model


def _fixture_mcp_manager(tmp_path: Path, *, risk: str = "passive") -> MCPManager:
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"version": 1, "servers": {"fixture": {
        "transport": "stdio",
        "stdio": {"source": "local_process", "command": sys.executable, "args": [str(fixture)]},
        "visibility": {"risk": risk},
    }}}), encoding="utf-8")
    manager = MCPManager(config_path=config, cache_path=tmp_path / "mcp-cache.json")
    manager.refresh()
    return manager


def _upload(client: TestClient, name: str = "task.txt", data: bytes = b"task material") -> str:
    response = client.post("/api/v2/input-uploads", params={"filename": name}, content=data)
    assert response.status_code == 201, response.text
    return response.json()["asset"]["id"]


def _create_request(task_id: str, *, mode: str = "ctf", task_ids: list[str] | None = None, hint: str | None = None) -> dict:
    return {
        "id": task_id,
        "name": task_id,
        "mode": mode,
        "goal": "analyze",
        "modeOptions": {"mode": mode},
        "input": {"text": hint or "", "fileIds": task_ids or []},
        "executionPolicy": ExecutionPolicy().model_dump(mode="json"),
    }


def _seed_session(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    task = TGATask(id="runtime_v2", name="runtime", mode="ctf", goal="solve", schema_version=6)
    store = EvidenceStore(tmp_path / "runs" / task.id / "evidence.db")
    try:
        store.create_task(task)
        store.create_session(SessionRecord(task_id=task.id, status="running", schema_version=6, workspace_path="workspace"))
        store.append_agent_event(task_id=task.id, type="SOLVER_STARTED", payload={"summary": "solver started"})
        store.append_agent_event(task_id=task.id, type="ACTION_PROPOSED", payload={"summary": "inspect login"})
    finally:
        store.close()
    return task.id


def test_new_session_automatically_snapshots_enabled_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    configure_verified_model(monkeypatch)
    monkeypatch.setattr(task_routes, "_schedule_runtime_runner", lambda _task_id: False)
    import tga.runtime.manager as runtime_manager

    runtime_manager._manager = None
    manager = _fixture_mcp_manager(tmp_path)
    monkeypatch.setattr(task_routes, "_catalog_runner", lambda: manager)
    client = TestClient(app)
    asset_id = _upload(client)
    payload = _create_request("selected_mcp", task_ids=[asset_id])
    created = client.post("/api/v2/tasks", json=payload)
    assert created.status_code == 200
    task = client.get("/api/v2/tasks/selected_mcp/session").json()["task"]
    assert "mcp" not in task["execution_policy"]
    assert task["mcp_capabilities"]["server_ids"] == ["fixture"]
    assert {item["method"] for item in task["mcp_capabilities"]["tools"]} == {"echo", "large_result"}


def test_new_session_snapshot_excludes_explicit_mcp_discovery_failure(monkeypatch):
    from tga.tools.mcp_config import MCPConfig, MCPServerConfig
    from tga.tools.mcp_registry import MCPCatalogSnapshot, MCPServerDiscovery

    class FailedManager:
        config = MCPConfig(servers={
            "failed": MCPServerConfig.model_validate({
                "enabled": True,
                "transport": "stdio",
                "stdio": {"source": "local_process", "command": "missing"},
            }),
        })

        def ensure_catalog(self):
            return MCPCatalogSnapshot(
                version="mcp_failed",
                servers=(MCPServerDiscovery(
                    server_id="failed", config_hash="test", discovered_at="2026-07-23T00:00:00Z",
                    status="configured", error={"code": "DISCOVERY_ERROR", "message": "unavailable"},
                ),),
            )

    snapshot = build_mcp_capability_snapshot(FailedManager())
    assert snapshot.server_ids == []
    assert snapshot.tools == []


def test_real_method_test_api_updates_shared_manager_health(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    manager = _fixture_mcp_manager(tmp_path, risk="active")
    monkeypatch.setattr(mcp_routes, "_catalog_runner", lambda: manager)
    client = TestClient(app)
    denied = client.post("/api/v2/mcp/servers/fixture/tools/echo/test", json={"arguments": {"text": "hello"}})
    assert denied.status_code == 409
    response = client.post("/api/v2/mcp/servers/fixture/tools/echo/test", json={"arguments": {"text": "hello"}, "confirm_active": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True and payload["request_id"] and payload["trace_id"]
    assert payload["explicit_active_authorization"] is True
    health = manager.status_snapshot()["records"][0]
    assert health["runnable"] is True and health["last_call_method"] == "echo"
    audit = json.loads((tmp_path / "runs" / "mcp-method-tests.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert audit["explicit_active_authorization"] is True and audit["request_id"] and audit["trace_id"]


def test_v2_task_creation_initializes_a_runtime_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    configure_verified_model(monkeypatch)
    monkeypatch.setattr(task_routes, "_schedule_runtime_runner", lambda task_id: task_id == "task_api")
    import tga.runtime.manager as runtime_manager

    runtime_manager._manager = None
    client = TestClient(app)
    asset_id = _upload(client)
    response = client.post("/api/v2/tasks", json=_create_request(
        "task_api", task_ids=[asset_id], hint="Inspect https://challenge.example/login before analyzing the supplied task.",
    ))

    assert response.status_code == 200
    assert response.json()["task_id"] == "task_api"
    assert response.json()["status"] == "created" and response.json()["scheduled"] is True
    snapshot = client.get("/api/v2/tasks/task_api/session").json()
    assert snapshot["session"]["status"] == "created"
    assert snapshot["task"]["session_input"]["prompt"] == "Inspect https://challenge.example/login before analyzing the supplied task."
    assert snapshot["task"]["schema_version"] == 6
    assert snapshot["task"]["skill_bundle_snapshot"] is None
    assert snapshot["task_common_skill_snapshot"]["selector"].startswith("task-common:")
    assert all(
        {"name", "version", "content_sha256", "selection_reasons"} <= set(item)
        for item in snapshot["task_common_skill_snapshot"]["skills"]
    )
    assert snapshot["task"]["task_entry_url"] == "https://challenge.example/login"
    assert snapshot["task"]["execution_policy"]["network"]["seed_origins"] == ["https://challenge.example"]
    assert {event["type"] for event in snapshot["events"]} >= {
        "TASK_INPUT_ANALYZED", "NETWORK_SEEDS_EXTRACTED", "MODEL_CONFIG_SNAPSHOTTED", "SOLVER_INSTANCE_CREATED",
    }
    assert snapshot["runtime"]["strategy_cards"] == []
    assert "test-key" not in json.dumps(snapshot)
    assert "runtime_ready" not in snapshot
    assert client.get("/api/tasks").status_code == 404
    assert client.get("/api/v2/tasks").json()["tasks"][0]["task_id"] == "task_api"
    report = client.get("/api/v2/tasks/task_api/report")
    assert report.status_code == 200
    assert "# TGA Report" in report.text
    report_path = tmp_path / "runs" / "task_api" / "reports" / "report.md"
    assert not report_path.exists()
    exported = client.post("/api/v2/tasks/task_api/report/export")
    assert exported.status_code == 200
    assert report_path.is_file()
    assert client.delete("/api/v2/tasks/task_api").json()["deleted"] is True


@pytest.mark.parametrize("mode", ["ctf", "penetration_test", "incident_response", "vulnerability_research", "reverse_engineering"])
def test_v2_api_accepts_and_outputs_each_current_mode(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    configure_verified_model(monkeypatch)
    monkeypatch.setattr(task_routes, "_schedule_runtime_runner", lambda _task_id: False)
    import tga.runtime.manager as runtime_manager
    runtime_manager._manager = None
    task_id = f"api_{mode}"
    client = TestClient(app)
    asset_id = _upload(client, name=f"{mode}.txt")
    response = client.post("/api/v2/tasks", json=_create_request(task_id, mode=mode, task_ids=[asset_id]))
    assert response.status_code == 200
    task = client.get(f"/api/v2/tasks/{task_id}/session").json()["task"]
    assert task["mode"] == mode
    assert task["schema_version"] == 6
    if mode != "ctf":
        assert task["flag_format"] is None


def test_v2_session_uses_only_agent_event_cursor(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)
    client = TestClient(app)

    session = client.get(f"/api/v2/tasks/{task_id}/session")
    assert session.status_code == 200
    assert [event["type"] for event in session.json()["events"]] == ["SOLVER_STARTED", "ACTION_PROPOSED"]
    events = client.get(f"/api/v2/tasks/{task_id}/events?after_seq=1")
    assert [item["seq"] for item in events.json()["events"]] == [2]


def test_action_snapshot_and_events_recursively_redact_tool_secrets(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)
    store = EvidenceStore(tmp_path / "runs" / task_id / "evidence.db")
    action = ActionSpec(
        id="act_secret", task_id=task_id, solver_id="solver_main",
        kind="tool", capability="mcp__fixture__login", target="fixture.login",
        arguments={
            "apiKey": "top-secret-key",
            "nested": {"password": "top-secret-password", "safe": "visible"},
        },
        rationale="test public projection", risk="active",
    )
    store.add_action(action, status="running")
    store.append_agent_event(
        task_id, "ACTION_PROPOSED",
        {"action_id": action.id, "arguments": action.arguments},
        solver_id="solver_main",
    )
    store.close()

    client = TestClient(app)
    snapshot = client.get(f"/api/v2/tasks/{task_id}/session").json()
    events = client.get(f"/api/v2/tasks/{task_id}/events?after_seq=0").json()
    encoded = json.dumps({"snapshot": snapshot, "events": events})

    assert "top-secret-key" not in encoded
    assert "top-secret-password" not in encoded
    projected = next(item for item in snapshot["actions"] if item["id"] == action.id)
    assert projected["arguments"]["apiKey"] == "[REDACTED]"
    assert projected["arguments"]["nested"] == {"password": "[REDACTED]", "safe": "visible"}
    assert "arguments_json" not in projected and "authorization_json" not in projected


def test_event_endpoint_never_builds_a_full_snapshot(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)
    monkeypatch.setattr(
        EvidenceStore,
        "task_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full snapshot was loaded")),
    )

    response = TestClient(app).get(f"/api/v2/tasks/{task_id}/events?after_seq=0")

    assert response.status_code == 200
    assert [event["seq"] for event in response.json()["events"]] == [1, 2]


def test_sse_stream_validation_never_builds_a_full_snapshot(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)
    checked: list[str] = []

    monkeypatch.setattr(event_routes, "_require_current_task", lambda value: checked.append(value))

    class Request:
        async def is_disconnected(self):
            return True

    response = asyncio.run(event_routes.stream_events(task_id, Request(), after_seq=2))

    assert response.media_type == "text/event-stream"
    assert checked == [task_id]


def test_running_task_cannot_be_deleted(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)

    response = TestClient(app).delete(f"/api/v2/tasks/{task_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == "running session cannot be deleted"


def test_v2_event_projection_omits_null_optional_payload_fields(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)
    store = EvidenceStore(tmp_path / "runs" / task_id / "evidence.db")
    try:
        store.conn.execute("INSERT INTO agent_events(id,task_id,solver_id,seq,type,payload_json,created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", ("evt_null", task_id, None, 3, "SESSION_CONTROLLED", '{"action":"resume","action_id":null,"status":"running"}', "2026-01-01T00:00:00Z"))
        store.conn.execute("UPDATE agent_event_sequences SET next_seq=4 WHERE task_id=?", (task_id,))
        store.conn.commit()
    finally:
        store.close()

    payload = TestClient(app).get(f"/api/v2/tasks/{task_id}/session").json()["events"][-1]["payload"]
    assert payload["action"] == "resume"
    assert "action_id" not in payload


def test_v2_snapshot_projection_omits_null_optional_context_metrics():
    snapshot = route_support._normalize_snapshot({
        "task": {"id": "task"},
        "session": {"status": "running", "turn_count": 1, "max_turns": 8},
        "context_metrics": [{
            "turn": 1,
            "audit_message_count": 2,
            "working_message_count": 3,
            "working_chars": 128,
            "summary_hits": 0,
            "artifact_retrievals": 0,
            "provider_input_tokens": None,
            "provider_output_tokens": None,
        }],
    })

    metric = snapshot["context_metrics"][0]
    assert metric["turn"] == 1
    assert "provider_input_tokens" not in metric
    assert "provider_output_tokens" not in metric


def test_v2_action_projection_keeps_in_flight_summary_a_string():
    action = route_support._normalize_action({
        "id": "act_running",
        "capability": "http.request",
        "target": "https://target.test/",
        "status": "running",
        "summary": None,
        "result": None,
    })

    assert action["summary"] == ""
    assert action["artifact_ids"] == []


def test_v2_action_projection_redacts_credentials_and_request_body():
    action = route_support._normalize_action({
        "id": "act_secret",
        "capability": "http.request",
        "target": "https://target.test/login",
        "status": "succeeded",
        "arguments": {
            "method": "POST",
            "path": "/login",
            "headers": {"Authorization": "Bearer private", "X-Trace": "safe"},
            "query": {"token": "private-query", "page": "1"},
            "body": {"password": "private-body"},
        },
    })

    encoded = str(action["arguments"])
    assert "private" not in encoded
    assert action["arguments"]["headers"]["Authorization"] == "[REDACTED]"
    assert action["arguments"]["query"]["token"] == "[REDACTED]"
    assert action["arguments"]["body"]["present"] is True


def test_v2_sse_stream_reads_agent_events(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    stream = event_routes._event_stream(task_id, ConnectedRequest(), cursor=0)
    try:
        first_chunk = asyncio.run(stream.__anext__())
    finally:
        asyncio.run(stream.aclose())

    assert "event: event" in first_chunk
    assert '"type": "SOLVER_STARTED"' in first_chunk


def test_v2_sse_disconnect_closes_transport_without_mutating_session(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)

    class DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    stream = event_routes._event_stream(task_id, DisconnectedRequest(), cursor=0)
    try:
        asyncio.run(stream.__anext__())
    except StopAsyncIteration:
        pass
    else:
        raise AssertionError("disconnected SSE stream should close immediately")

    status = TestClient(app).get(f"/api/v2/tasks/{task_id}/session").json()["session"]["status"]
    assert status == "running"


def test_v2_start_recovers_a_created_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    configure_verified_model(monkeypatch)
    task = TGATask(id="recover", name="recover", mode="ctf", goal="solve", schema_version=6)
    store = EvidenceStore(tmp_path / "runs" / task.id / "evidence.db")
    try:
        store.create_task(task)
        store.create_session(SessionRecord(task_id=task.id, status="created", schema_version=6, workspace_path="workspace"))
    finally:
        store.close()
    monkeypatch.setattr(session_routes, "_schedule_runtime_runner", lambda value: value == task.id)
    import tga.runtime.manager as runtime_manager

    runtime_manager._manager = None
    response = TestClient(app).post(f"/api/v2/tasks/{task.id}/start", json={"initial_hint": "Check the documented login form first."})
    assert response.json() == {"accepted": True, "status": "created", "scheduled": True}


def test_v2_control_projects_stable_invalid_transition_code(monkeypatch):
    class RuntimeService:
        def __init__(self, **_kwargs):
            pass

        def command(self, *_args, **_kwargs):
            raise SessionTransitionError("completed", "running")

    configure_verified_model(monkeypatch)
    monkeypatch.setattr(session_routes, "_snapshot", lambda _task_id: {})
    monkeypatch.setattr(route_support, "TaskRuntimeService", RuntimeService)

    response = TestClient(app).post("/api/v2/tasks/completed/control", json={"action": "resume"})

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "SESSION_TRANSITION_INVALID",
        "from_status": "completed",
        "to_status": "running",
        "message": "invalid session transition: completed -> running",
    }


def test_v2_task_creation_requires_a_configured_model(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.delenv("TGA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TGA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TGA_LLM_MODEL", raising=False)

    response = TestClient(app).post("/api/v2/tasks", json=_create_request("model_required", task_ids=["asset_" + "a" * 32]))

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "MODEL_NOT_CONFIGURED", "message": "model_not_configured"}
    assert not (tmp_path / "runs" / "model_required").exists()


def test_v2_settings_and_capabilities_routes(monkeypatch):
    monkeypatch.delenv("TGA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TGA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TGA_LLM_MODEL", raising=False)
    client = TestClient(app)
    assert client.get("/api/v2/settings/llm").json()["configured"] is False
    updated = client.post("/api/v2/settings/llm", json={"base_url": "https://example.test/v1", "api_key": "browser-secret", "model": "demo-model", "supports_vision": True})
    assert updated.status_code == 200
    assert updated.json()["configured"] is True
    assert updated.json()["api_key_set"] is True
    assert updated.json()["browser_configured"] is True
    assert updated.json()["supports_vision"] is True
    assert "api_key" not in updated.json()
    read_back = client.get("/api/v2/settings/llm").json()
    assert read_back["configured"] is True
    assert "api_key" not in read_back
    preserved = client.post("/api/v2/settings/llm", json={"base_url": "https://example.test/v1", "model": "second-model", "supports_vision": False})
    assert preserved.json()["configured"] is True
    assert preserved.json()["api_key_set"] is True
    monkeypatch.delenv("TGA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TGA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TGA_LLM_MODEL", raising=False)

    runner = object()
    class FakeRegistry:
        def snapshot(self): return {"capabilities": [{"name": "workspace.write", "risk": "active"}]}
    monkeypatch.setattr(capability_routes, "build_default_registry", lambda: FakeRegistry())
    monkeypatch.setattr(capability_routes, "_catalog_runner", lambda: runner)
    monkeypatch.setattr(capability_routes, "tool_catalog_snapshot", lambda value: {"tools": []} if value is runner else {})
    monkeypatch.setattr(capability_routes, "health_snapshot", lambda value: {"records": []} if value is runner else {})
    assert client.get("/api/v2/capabilities").json()["capabilities"][0]["name"] == "workspace.write"
    assert client.get("/api/v2/tools/health").json()["configured"] is True
    skills = client.get("/api/v2/settings/skills").json()
    prompts = client.get("/api/v2/settings/agent-prompts").json()
    assert skills["schema_version"] == 3 and skills["skills"]
    assert prompts["schema_version"] == 1
    assert {item["id"] for item in prompts["modes"]} == {"ctf", "penetration_test", "incident_response", "vulnerability_research", "reverse_engineering"}
    assert client.get("/api/v2/settings/prompts").status_code == 404


def test_mcp_catalog_refresh_route(monkeypatch):
    class FakeManager:
        def __init__(self): self.refreshed = False
        def refresh(self): self.refreshed = True
        def status_snapshot(self): return {"configured": True, "catalog_version": "mcp_next", "records": []}
    manager = FakeManager()
    monkeypatch.setattr(mcp_routes, "_catalog_runner", lambda: manager)
    response = TestClient(app).post("/api/v2/tools/mcp/refresh")
    assert response.status_code == 200
    assert response.json()["catalog_version"] == "mcp_next"
    assert manager.refreshed is True


def test_mcp_import_route_streams_file_and_refreshes_catalog(monkeypatch, tmp_path):
    from tga.tools.mcp_importer import MCPImportResult

    class FakeManager:
        config_path = tmp_path / "mcp.json"

        def __init__(self): self.refreshed = False
        def refresh(self): self.refreshed = True
        def status_snapshot(self): return {"configured": True, "records": [{"server": "demo", "discovered": True, "tools": 2}]}

    class FakeImporter:
        def __init__(self, **kwargs): assert kwargs["config_path"] == manager.config_path
        def import_package(self, path, filename):
            assert Path(path).read_bytes() == b"docker archive"
            assert filename == "demo image.tar"
            return MCPImportResult(server_id="demo", image="demo-mcp:latest", source_type="docker-image", config_path=str(manager.config_path), config_action="created")

    manager = FakeManager()
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(mcp_routes, "_catalog_runner", lambda: manager)
    monkeypatch.setattr(mcp_routes, "MCPImageImporter", FakeImporter)
    response = TestClient(app).post(
        "/api/v2/tools/mcp/import",
        content=b"docker archive",
        headers={"X-TGA-Filename": "demo%20image.tar", "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200
    assert response.json()["server_id"] == "demo"
    assert response.json()["catalog"]["records"][0]["tools"] == 2
    assert manager.refreshed is True


def test_mcp_delete_route_removes_config_but_not_image(monkeypatch, tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        '{"version":1,"servers":{"demo":{"transport":"stdio","stdio":{"source":"docker_image","image":"demo-mcp:latest"}}}}',
        encoding="utf-8",
    )

    class FakeManager:
        def __init__(self): self.config_path = config_path; self.refreshed = False
        def refresh(self): self.refreshed = True
        def status_snapshot(self): return {"configured": True, "records": []}

    manager = FakeManager()
    monkeypatch.setattr(mcp_routes, "_catalog_runner", lambda: manager)
    response = TestClient(app).delete("/api/v2/tools/mcp/demo")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["image_deleted"] is False
    assert json.loads(config_path.read_text(encoding="utf-8"))["servers"] == {}
    assert manager.refreshed is True


def test_mcp_enabled_route_updates_config_and_refreshes(monkeypatch, tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        '{"version":1,"servers":{"demo":{"enabled":false,"transport":"stdio","stdio":{"source":"docker_image","image":"demo-mcp:latest"}}}}',
        encoding="utf-8",
    )

    class FakeManager:
        def __init__(self): self.config_path = config_path; self.refreshed = False
        def refresh(self): self.refreshed = True
        def status_snapshot(self): return {"configured": True, "records": [{"server": "demo", "enabled": True, "discovered": True, "tools": 2}]}

    manager = FakeManager()
    monkeypatch.setattr(mcp_routes, "_catalog_runner", lambda: manager)
    response = TestClient(app).patch("/api/v2/tools/mcp/demo/enabled", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert json.loads(config_path.read_text(encoding="utf-8"))["servers"]["demo"]["enabled"] is True
    assert manager.refreshed is True


def test_full_mcp_management_api_crud_and_redaction(monkeypatch, tmp_path):
    from tga.tools.mcp_registry import MCPDiscoveredTool, MCPServerDiscovery

    config_path = tmp_path / "mcp.json"
    config_path.write_text('{"version":1,"servers":{}}', encoding="utf-8")

    class FakeManager:
        def __init__(self): self.config_path = config_path
        def refresh(self): return None
        def status_snapshot(self):
            config = json.loads(config_path.read_text(encoding="utf-8"))
            return {"configured": True, "records": [{"server": key, "enabled": value["enabled"]} for key, value in config["servers"].items()]}
        def test_server(self, server_id):
            return MCPServerDiscovery(
                server_id=server_id,
                config_hash="test",
                protocol_version="2024-11-05",
                server_info={"name": "remote"},
                tools=(MCPDiscoveredTool(name="scan", description="Scan target"),),
                discovered_at="2026-01-01T00:00:00Z",
            )

    monkeypatch.setattr(mcp_routes, "_catalog_runner", lambda: FakeManager())
    client = TestClient(app)
    created = client.post(
        "/api/v2/mcp/servers",
        json={
            "id": "remote",
            "config": {
                "enabled": False,
                "transport": "streamable_http",
                "http": {
                    "url": "https://mcp.example.test/mcp?token=hidden",
                    "secretRefs": {"Authorization": "env:MCP_REMOTE_TOKEN"},
                },
            },
        },
    )
    assert created.status_code == 201
    assert created.json()["server"]["config"]["http"]["url"].endswith("?redacted")
    assert created.json()["server"]["config"]["http"]["secretRefs"] == {"Authorization": "env:MCP_REMOTE_TOKEN"}

    tools = client.get("/api/v2/mcp/servers/remote/tools")
    assert tools.status_code == 200
    assert tools.json()["tools"][0]["name"] == "scan"
    assert tools.json()["protocol_version"] == "2024-11-05"

    patched = client.patch("/api/v2/mcp/servers/remote", json={"enabled": True, "enabledTools": ["scan"]})
    assert patched.status_code == 200
    persisted = json.loads(config_path.read_text(encoding="utf-8"))["servers"]["remote"]
    assert persisted["enabled"] is True
    assert persisted["enabledTools"] == ["scan"]
    assert "stdio" not in persisted

    assert client.delete("/api/v2/mcp/servers/remote").status_code == 200
    assert json.loads(config_path.read_text(encoding="utf-8"))["servers"] == {}


@pytest.mark.parametrize(
    ("config_body", "expected_status", "expected_code"),
    [
        (None, 503, "MCP_CONFIG_NOT_FOUND"),
        ('{"version":1,"servers":{"broken":{"transport":"stdio","stdio":{"source":"local_process","command":"x"},"visibility":{"modes":["not-a-mode"]}}}}', 422, "MCP_CONFIG_INVALID"),
    ],
)
def test_mcp_server_list_returns_structured_config_errors(monkeypatch, tmp_path, config_body, expected_status, expected_code):
    config_path = tmp_path / "mcp.json"
    if config_body is not None:
        config_path.write_text(config_body, encoding="utf-8")

    class FakeManager:
        def __init__(self):
            self.config_path = config_path

    monkeypatch.setattr(mcp_routes, "_catalog_runner", lambda: FakeManager())
    response = TestClient(app).get("/api/v2/mcp/servers")

    assert response.status_code == expected_status
    detail = response.json()["detail"]
    assert detail["code"] == expected_code
    assert detail["message"].startswith("MCP configuration")
    assert detail["reason"]


def test_v2_llm_verify_reports_connectivity_without_exposing_response(monkeypatch):
    class FakeClient:
        model = "reachable-model"
        supports_vision = False
        reasoning_mode = "auto"

        def chat(self, messages, *, temperature):
            assert messages[0].content
            return type("Response", (), {"content": "ok", "raw": {"id": "basic_request"}})()

        def chat_action_tool(self, messages, *, tool_name, tool_description, parameters, thinking, temperature):
            assert tool_name == "verify_tga_action_protocol"
            assert [item.role for item in messages] == ["system", "user"]
            return type("Response", (), {"content": '{"ok":true}', "raw": {"id": "forced_request"}})()

        def preflight_tools(self, tools):
            assert tools[0]["function"]["name"] == "verify_tga_action_protocol"
            return {"ok": True, "request_id": "auto_request"}

        def chat_tools(self, messages, *, tools, temperature, max_tokens):
            assert len(tools) > 1
            return {"message": {"content": "accepted"}, "request_id": "catalog_request"}

    configure_verified_model(monkeypatch)
    monkeypatch.setattr(llm_routes, "build_model_client", lambda: FakeClient())
    assert ProviderCapabilityProbe(FakeClient()).verify()["tool_catalog"]["accepted"] is True

    response = TestClient(app).post("/api/v2/settings/llm/verify")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["configured"] is True
    assert payload["reachable"] is True
    assert payload["action_tools"] is True
    assert payload["model"] == "reachable-model"
    assert payload["request_id"] == "basic_request"
    assert payload["verification_status"] == "verified"
    assert payload["capabilities"] == {
        "chat_completions": True, "tool_calling": True,
        "forced_tool_choice": True, "auto_tool_choice": True,
        "vision": False, "reasoning_content": True,
    }
    assert payload["tool_catalog"]["accepted"] is True
    assert payload["tool_catalog"]["tool_count"] > 1
    assert payload["tool_catalog"]["schema_bytes"] > 0
