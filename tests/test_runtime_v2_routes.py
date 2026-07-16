import asyncio

from fastapi.testclient import TestClient

from apps.api import routes_v2
from apps.api.main import app
from tga.contracts import SessionRecord, TGATask
from tga.evidence.store import EvidenceStore


def _seed_session(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    task = TGATask(id="runtime_v2", name="runtime", mode="ctf", target="http://127.0.0.1:8080", scope=["127.0.0.1:8080"], goal="solve")
    store = EvidenceStore(tmp_path / "runs" / task.id / "evidence.db")
    try:
        store.create_task(task)
        store.create_session(SessionRecord(task_id=task.id, status="running"))
        store.append_agent_event(task_id=task.id, type="SOLVER_STARTED", payload={"summary": "solver started"})
        store.append_agent_event(task_id=task.id, type="ACTION_PROPOSED", payload={"summary": "inspect login"})
    finally:
        store.close()
    return task.id


def test_v2_task_creation_initializes_a_runtime_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("TGA_LLM_BASE_URL", "https://model.test/v1")
    monkeypatch.setenv("TGA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("TGA_LLM_MODEL", "test-model")
    monkeypatch.setattr(routes_v2, "_schedule_runtime_runner", lambda task_id: task_id == "task_api")
    import tga.runtime.manager as runtime_manager

    runtime_manager._manager = None
    client = TestClient(app)
    response = client.post("/api/v2/tasks", json={"task": {"id": "task_api", "name": "api-demo", "mode": "ctf", "target": "http://127.0.0.1:1", "scope": ["127.0.0.1:1"], "intensity": "normal", "allow_active_scan": False, "goal": "solve", "flag_format": "flag\\{[^}]+\\}"}, "initial_hint": "Inspect the login form first."})

    assert response.status_code == 200
    assert response.json() == {"task_id": "task_api", "status": "created", "scheduled": True}
    snapshot = client.get("/api/v2/tasks/task_api/session").json()
    assert snapshot["session"]["status"] == "created"
    assert snapshot["board"]["memory"][0]["content"] == "Inspect the login form first."
    assert "runtime_ready" not in snapshot
    assert client.get("/api/tasks").status_code == 404
    assert client.get("/api/v2/tasks").json()["tasks"][0]["task_id"] == "task_api"
    report = client.get("/api/v2/tasks/task_api/report")
    assert report.status_code == 200
    assert "# TGA Report" in report.text
    assert client.delete("/api/v2/tasks/task_api").json()["deleted"] is True


def test_v2_session_uses_only_agent_event_cursor(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)
    client = TestClient(app)

    session = client.get(f"/api/v2/tasks/{task_id}/session")
    assert session.status_code == 200
    assert [event["type"] for event in session.json()["events"]] == ["SOLVER_STARTED", "ACTION_PROPOSED"]
    events = client.get(f"/api/v2/tasks/{task_id}/events?after_seq=1")
    assert [item["seq"] for item in events.json()["events"]] == [2]


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


def test_v2_action_projection_keeps_in_flight_summary_a_string():
    action = routes_v2._normalize_action({
        "id": "act_running",
        "capability": "http.request",
        "target": "https://target.test/",
        "status": "running",
        "summary": None,
        "result": None,
    })

    assert action["summary"] == ""
    assert action["artifact_ids"] == []


def test_v2_sse_stream_reads_agent_events(tmp_path, monkeypatch):
    task_id = _seed_session(tmp_path, monkeypatch)

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    stream = routes_v2._event_stream(task_id, ConnectedRequest(), cursor=0)
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

    stream = routes_v2._event_stream(task_id, DisconnectedRequest(), cursor=0)
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
    monkeypatch.setenv("TGA_LLM_BASE_URL", "https://model.test/v1")
    monkeypatch.setenv("TGA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("TGA_LLM_MODEL", "test-model")
    task = TGATask(id="recover", name="recover", mode="ctf", target="http://127.0.0.1:8080", scope=["127.0.0.1:8080"], goal="solve")
    store = EvidenceStore(tmp_path / "runs" / task.id / "evidence.db")
    try:
        store.create_task(task)
        store.create_session(SessionRecord(task_id=task.id, status="created"))
    finally:
        store.close()
    monkeypatch.setattr(routes_v2, "_schedule_runtime_runner", lambda value: value == task.id)
    import tga.runtime.manager as runtime_manager

    runtime_manager._manager = None
    response = TestClient(app).post(f"/api/v2/tasks/{task.id}/start", json={"initial_hint": "Check the documented login form first."})
    assert response.json() == {"accepted": True, "status": "created", "scheduled": True}


def test_v2_task_creation_requires_a_configured_model(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.delenv("TGA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TGA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TGA_LLM_MODEL", raising=False)

    response = TestClient(app).post("/api/v2/tasks", json={"task": {
        "id": "model_required", "name": "model required", "mode": "ctf",
        "target": "http://127.0.0.1:8080", "scope": ["127.0.0.1:8080"], "goal": "solve",
    }})

    assert response.status_code == 409
    assert response.json()["detail"] == "model_not_configured"
    assert not (tmp_path / "runs" / "model_required").exists()


def test_v2_settings_and_capabilities_routes(monkeypatch):
    monkeypatch.delenv("TGA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TGA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TGA_LLM_MODEL", raising=False)
    client = TestClient(app)
    assert client.get("/api/v2/settings/llm").json()["configured"] is False
    updated = client.post("/api/v2/settings/llm", json={"base_url": "https://example.test/v1", "api_key": "secret", "model": "demo-model"})
    assert updated.json()["configured"] is True
    monkeypatch.delenv("TGA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TGA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TGA_LLM_MODEL", raising=False)

    runner = object()
    class FakeRegistry:
        def snapshot(self): return {"capabilities": [{"name": "workspace.write", "risk": "active"}]}
    monkeypatch.setattr(routes_v2, "build_default_registry", lambda: FakeRegistry())
    monkeypatch.setattr(routes_v2, "_catalog_runner", lambda: runner)
    monkeypatch.setattr(routes_v2, "tool_catalog_snapshot", lambda value: {"tools": []} if value is runner else {})
    monkeypatch.setattr(routes_v2, "health_snapshot", lambda value: {"records": []} if value is runner else {})
    assert client.get("/api/v2/capabilities").json()["capabilities"][0]["name"] == "workspace.write"
    assert client.get("/api/v2/tools/health").json()["configured"] is True
    skills = client.get("/api/v2/settings/skills").json()
    prompts = client.get("/api/v2/settings/prompts").json()
    assert skills["schema_version"] == 2 and skills["skills"]
    assert {item["role"] for item in prompts["prompts"]} == {"main", "recon", "targeted", "research"}
    assert all(item["editable"] is False for item in prompts["prompts"])


def test_v2_llm_verify_reports_connectivity_without_exposing_response(monkeypatch):
    class FakeClient:
        model = "reachable-model"

        def chat_action_tool(self, messages, **kwargs):
            assert messages[-1].content == "Confirm the TGA action tool protocol."
            assert kwargs["tool_name"] == "verify_tga_action_protocol"
            assert kwargs["parameters"]["required"] == ["ok"]
            assert kwargs["temperature"] == 0
            return type("Response", (), {"content": '{"ok":true}'})()

    monkeypatch.setattr(routes_v2, "build_model_client_from_env", lambda: FakeClient())

    response = TestClient(app).post("/api/v2/settings/llm/verify")

    assert response.json() == {"configured": True, "reachable": True, "action_tools": True, "model": "reachable-model"}
