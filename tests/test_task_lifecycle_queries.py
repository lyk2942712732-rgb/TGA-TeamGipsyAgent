from fastapi.testclient import TestClient

from apps.api.main import app
from tga.contracts import SessionRecord, TGATask
from tga.domain.task.spec import TaskSpec
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.service import TaskRuntimeService


def _seed(tmp_path, monkeypatch, *, task_id: str = "lifecycle_task") -> str:
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    task = TGATask(
        id=task_id,
        name="Lifecycle task",
        mode="ctf",
        goal="Recover verified evidence",
        schema_version=6,
    )
    store = EvidenceStore(run_root / task_id / "evidence.db")
    try:
        store.create_task(task)
        store.create_session(SessionRecord(
            task_id=task_id, status="awaiting_approval", turn_count=2,
            max_turns=20, schema_version=6,
        ))
        PersistenceBundle(store).tasks.save_task_spec(TaskSpec(
            task_id=task_id, objective=task.goal,
        ))
        store.append_agent_event(task_id, "TEST_EVENT", {"summary": "review"})
    finally:
        store.close()
    return task_id


def test_task_list_uses_lightweight_query_and_real_filters(tmp_path, monkeypatch):
    task_id = _seed(tmp_path, monkeypatch)

    def forbidden_snapshot(*_args, **_kwargs):
        raise AssertionError("task list must not assemble Runtime snapshots")

    monkeypatch.setattr(TaskRuntimeService, "runtime_snapshot", forbidden_snapshot)
    client = TestClient(app)
    response = client.get(
        "/api/v2/tasks",
        params={
            "query": "Lifecycle", "mode": "ctf",
            "status": "awaiting_approval", "needs_attention": True,
            "offset": 0, "limit": 100,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["next_offset"] is None
    assert payload["tasks"][0] == {
        **payload["tasks"][0],
        "task_id": task_id,
        "status": "awaiting_approval",
        "needs_attention": True,
        "turn_count": 2,
        "max_turns": 20,
    }


def test_task_detail_is_lightweight_and_excludes_runtime_entities(tmp_path, monkeypatch):
    task_id = _seed(tmp_path, monkeypatch)

    def forbidden_snapshot(*_args, **_kwargs):
        raise AssertionError("task detail must not assemble Runtime snapshots")

    monkeypatch.setattr(TaskRuntimeService, "runtime_snapshot", forbidden_snapshot)
    response = TestClient(app).get(f"/api/v2/tasks/{task_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["goal"] == "Recover verified evidence"
    assert payload["task_spec"]["objective"] == "Recover verified evidence"
    assert payload["lifecycle"]["status"] == "awaiting_approval"
    assert payload["lifecycle"]["needs_attention"] is True
    assert set(payload) == {
        "schema_version", "task_id", "task", "task_spec",
        "lifecycle", "input_summary", "config_snapshot",
    }
    for forbidden in ("session", "team", "solvers", "intents", "events", "approvals"):
        assert forbidden not in payload


def test_task_inputs_no_longer_require_runtime_snapshot(tmp_path, monkeypatch):
    task_id = _seed(tmp_path, monkeypatch)

    def forbidden_snapshot(*_args, **_kwargs):
        raise AssertionError("input manifest must read the Task definition")

    monkeypatch.setattr(TaskRuntimeService, "runtime_snapshot", forbidden_snapshot)
    response = TestClient(app).get(f"/api/v2/tasks/{task_id}/inputs")
    assert response.status_code == 200
    assert response.json()["task_goal"] == "Recover verified evidence"
