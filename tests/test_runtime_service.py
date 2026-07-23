import json
import sqlite3
from pathlib import Path

from tga.contracts import TGATask
from tga.evidence.store import EvidenceStore
from tga.runtime.manager import Manager
from tga.runtime.observer import ObserverPatch, ObserverSidecar, build_observer_context
from tga.runtime.service import TaskRuntimeService
from tga.runtime.solver import MainSolver


def _task(task_id: str = "task_service") -> TGATask:
    return TGATask(
        id=task_id,
        name="service task",
        mode="ctf",
        target="http://target.local",
        scope=["target.local"],
        goal="collect artifact-backed proof",
        flag_format=r"flag\{[^}]+\}",
    )


def test_runtime_service_owns_create_query_control_and_versioned_events(tmp_path: Path) -> None:
    manager = Manager(run_root=tmp_path, solver=MainSolver())
    service = TaskRuntimeService(run_root=tmp_path, manager=manager)

    created = service.create_task(_task(), initial_hint="known boundary")
    assert created["schema_version"] == 2
    assert service.snapshot("task_service")["session"]["status"] == "created"
    assert "intensity" not in service.list_tasks()[0]

    cancelled = service.command("control_session", "task_service", action="cancel")
    assert cancelled == {"accepted": True, "status": "cancelled"}
    events = service.events("task_service")
    assert [item["type"] for item in events][-1] == "SESSION_CONTROLLED"
    assert all(item["schema_version"] == 2 for item in events)


def test_agent_event_schema_migrates_existing_database(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence.db")
    store.create_task(_task("task_event_version"))
    event = store.append_agent_event(task_id="task_event_version", type="TEST", payload={"value": 1})
    store.close()

    assert event.schema_version == 2
    reopened = EvidenceStore(tmp_path / "evidence.db")
    assert reopened.list_agent_events("task_event_version")[0].schema_version == 2
    reopened.close()


def test_governance_schema_additively_migrates_existing_action_table(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE sessions (task_id TEXT PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE agent_events (
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, solver_id TEXT, seq INTEGER NOT NULL,
          type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE actions (
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, solver_id TEXT NOT NULL,
          hypothesis_id TEXT, kind TEXT NOT NULL, capability TEXT NOT NULL,
          target TEXT NOT NULL, arguments_json TEXT NOT NULL, rationale TEXT NOT NULL,
          risk TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    connection.close()

    store = EvidenceStore(db_path)
    action_columns = {row["name"] for row in store.conn.execute("PRAGMA table_info(actions)")}
    assert {
        "strategy_card_id", "strategy_step_id", "expected_outcome", "retry_reason",
        "alternative_analysis", "expected_side_effects",
    } <= action_columns
    assert store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_cards'"
    ).fetchone()
    assert store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_indexes'"
    ).fetchone()
    store.close()


def test_observer_context_is_bounded_redacted_and_duplicate_patch_is_cooled_down() -> None:
    snapshot = {
        "task": {"id": "task", "name": "demo", "mode": "ctf", "goal": "token=super-secret"},
        "session": {"status": "running", "turn_count": 1, "max_turns": 8},
        "actions": [{"id": "a", "arguments": {"authorization": "Bearer leak"}, "result": {"summary": "token=secret-value", "artifact_ids": ["art"]}}],
        "board": {"hypotheses": [], "memory": [{"id": "m", "kind": "fact", "content": "password=hunter2", "artifact_ids": ["art"], "source": "solver"}]},
    }
    context = build_observer_context(snapshot)
    encoded = json.dumps(context)
    assert "super-secret" not in encoded
    assert "secret-value" not in encoded
    assert "hunter2" not in encoded
    assert "arguments" not in encoded

    class FixedObserver:
        def review(self, _snapshot):
            return ObserverPatch(steer_message="switch route")

    sidecar = ObserverSidecar(FixedObserver(), cooldown_seconds=60)
    try:
        assert sidecar.request(context)
        assert sidecar.drain(wait=True) is not None
        assert sidecar.request(context)
        assert sidecar.drain(wait=True) is None
    finally:
        sidecar.close()
