from __future__ import annotations

from pathlib import Path

import pytest

from tga.contracts import ActionSpec, ArtifactRecord, SessionRecord, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore, utc_now
from tga.runtime.coordinator import SessionCoordinator, SessionTransitionError
from tga.domain.governance.models import ActionEffect
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.tooling.requests import ActionContext, AuthorizationDecision, GovernedAction
from tga.runtime.tooling.results import RawExecutionResult
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.runtime.orchestration import TaskOrchestrator


def _task(task_id: str = "tx_task") -> TGATask:
    return TGATask(
        id=task_id,
        name="transaction test",
        mode="ctf",
        goal="verify atomic persistence",
        flag_format=r"CTF\{[^}]+\}",
    )


def _store(tmp_path: Path, task: TGATask) -> EvidenceStore:
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    return store


def _ensure_session(store: EvidenceStore, task: TGATask) -> tuple[SessionCoordinator, str]:
    bundle = PersistenceBundle(store)
    state = TaskOrchestrator(task=task, repositories=bundle).bootstrap()
    assert state.supervisor_solver_id is not None
    coordinator = SessionCoordinator(store)
    coordinator.ensure_session(
        task=task, max_turns=4,
        supervisor_solver_id=state.supervisor_solver_id,
    )
    return coordinator, state.supervisor_solver_id


def test_nested_transaction_exception_marks_outer_transaction_rollback_only(tmp_path: Path) -> None:
    task = _task("nested_rollback")
    store = _store(tmp_path, task)
    with store.transaction():
        store.append_agent_event(task.id, "OUTER_EVENT", {"value": 1})
        try:
            with store.transaction():
                store.append_agent_event(task.id, "INNER_EVENT", {"value": 2})
                raise RuntimeError("inject nested failure")
        except RuntimeError:
            pass
        store.append_agent_event(task.id, "OUTER_AFTER_EVENT", {"value": 3})

    assert store.conn.execute(
        "SELECT COUNT(*) FROM agent_events WHERE task_id=?", (task.id,)
    ).fetchone()[0] == 0
    store.close()


def test_running_transition_updates_session_challenge_and_events_atomically(tmp_path: Path) -> None:
    task = _task("atomic_start")
    store = _store(tmp_path, task)
    coordinator, solver_id = _ensure_session(store, task)

    session = coordinator.start(task_id=task.id, solver_id=solver_id)

    assert session.status == "running"
    assert PersistenceBundle(store).solvers.get_solver(solver_id).status == "created"
    assert store.get_challenge(task.id).status == "active"  # type: ignore[union-attr]
    assert {event.type for event in store.list_agent_events(task.id)} >= {
        "CHALLENGE_STATUS_CHANGED",
        "SESSION_STARTED",
        "AGENT_STARTED",
    }
    store.close()


def test_invalid_session_transition_has_stable_error_code(tmp_path: Path) -> None:
    task = _task("invalid_transition")
    store = _store(tmp_path, task)
    coordinator = SessionCoordinator(store)
    coordinator.create(SessionRecord(task_id=task.id, status="completed", max_turns=4))

    with pytest.raises(SessionTransitionError) as raised:
        coordinator.resume(task_id=task.id)

    assert raised.value.code == "SESSION_TRANSITION_INVALID"
    assert raised.value.from_status == "completed"
    assert raised.value.to_status == "running"
    store.close()


def test_running_session_restart_preserves_formal_solver_lifecycle(tmp_path: Path) -> None:
    task = _task("restart_solver")
    store = _store(tmp_path, task)
    coordinator, solver_id = _ensure_session(store, task)
    bundle = PersistenceBundle(store)
    bundle.solvers.update_solver_status(solver_id, "waiting")
    store.update_session(task.id, status="running")

    session = coordinator.start(task_id=task.id, solver_id=solver_id)

    assert session.status == "running"
    assert bundle.solvers.get_solver(solver_id).status == "waiting"
    store.close()


def test_coordinator_releases_terminal_runtime_resources(tmp_path: Path) -> None:
    task = _task("release_resources")
    store = _store(tmp_path, task)
    coordinator = SessionCoordinator(store)
    closed: list[str] = []

    class Handlers:
        def close(self):
            closed.append("handlers")

    class Executor:
        def close_http_sessions(self, *, task_id: str, solver_id: str | None):
            assert task_id == task.id and solver_id == "solver_main"
            closed.append("http")
            return ["https://target.test"]

    class MCPManager:
        def close(self):
            closed.append("mcp")

    coordinator.release_resources(
        task_id=task.id,
        solver_id="solver_main",
        status="completed",
        handlers=Handlers(),
        executor=Executor(),
        mcp_manager=MCPManager(),
    )

    assert closed == ["handlers", "http", "mcp"]
    event = store.list_agent_events(task.id)[-1]
    assert event.type == "HTTP_SESSION_STATUS"
    assert event.payload["destroyed_origins"] == ["https://target.test"]
    store.close()


def test_schema_v6_governed_action_result_is_idempotent_and_immutable(tmp_path: Path) -> None:
    task = _task("immutable_action_result")
    store = _store(tmp_path, task)
    now = utc_now()
    action = GovernedAction(
        id="governed_immutable",
        context=ActionContext(
            task_id=task.id,
            solver_id="solver_main",
            orchestration_role="worker",
            solver_definition_id="worker-generalist",
            execution_policy_snapshot_id="execution:" + "a" * 64,
            solver_tool_policy_snapshot_id="tool:" + "b" * 64,
            created_at=now,
        ),
        provider_tool_name="workspace_read",
        tool_call_id="call_immutable",
        tool_class="resource_read",
        capability="workspace.read",
        resolved_target="workspace:solver_main:input.txt",
        normalized_arguments={"relative_path": "input.txt"},
        rationale="read task evidence",
        risk="passive",
        effect=ActionEffect(),
        authorization=AuthorizationDecision(allowed=True, reason="test"),
        status="running",
        created_at=now,
        updated_at=now,
    )
    first = RawExecutionResult(
        action_id=action.id,
        status="succeeded",
        output={"ok": True, "summary": "original result"},
        artifact_ids=[],
    )
    conflicting = first.model_copy(update={"output": {"ok": True, "summary": "different result"}})
    repository = PersistenceBundle(store).tool_governance
    try:
        repository.add_action(action)
        repository.save_result(action.id, first)
        repository.save_result(action.id, first)

        with pytest.raises(PersistenceConflict, match="immutable"):
            repository.save_result(action.id, conflicting)

        assert repository.get_action(action.id)["result"]["output"]["summary"] == "original result"
        assert store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='actions'"
        ).fetchone() is None
    finally:
        store.close()


def test_artifact_replace_failure_leaves_no_partial_or_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "artifacts"
    artifacts = ArtifactStore(root)

    def fail_replace(_source, _target) -> None:
        raise OSError("inject atomic replace failure")

    monkeypatch.setattr("tga.evidence.artifacts.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        artifacts.save_text(
            task_id="artifact_failure",
            intent_id=None,
            kind="tool_output",
            text="never committed",
        )

    assert list(root.iterdir()) == []


def test_completion_event_failure_rolls_back_session_solver_challenge_and_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task("completion_rollback")
    store = _store(tmp_path, task)
    coordinator, solver_id = _ensure_session(store, task)
    PersistenceBundle(store).solvers.update_solver_status(solver_id, "running")
    coordinator.start(task_id=task.id, solver_id=solver_id)
    artifact = ArtifactRecord(
        id="artifact_proof",
        task_id=task.id,
        kind="tool_output",
        path="proof.txt",
        sha256="a" * 64,
        created_at=utc_now(),
    )
    store.add_artifact(artifact)
    original_append = store.append_agent_event

    def fail_terminal_event(task_id: str, event_type: str, payload: dict, *, solver_id: str | None = None):
        if event_type == "AGENT_FINISHED":
            raise RuntimeError("inject completion event failure")
        return original_append(task_id, event_type, payload, solver_id=solver_id)

    monkeypatch.setattr(store, "append_agent_event", fail_terminal_event)
    with pytest.raises(RuntimeError, match="completion event failure"):
        coordinator.complete(
            task_id=task.id,
            summary="verified",
            evidence_artifact_ids=[artifact.id],
            turn_count=2,
            solver_id=solver_id,
            details={
                "structured_result": {
                    "flag": "CTF{atomic}",
                    "proof_artifact_id": artifact.id,
                    "verification": "completion_validator",
                }
            },
        )

    assert store.get_session(task.id).status == "running"  # type: ignore[union-attr]
    assert PersistenceBundle(store).solvers.get_solver(solver_id).status == "running"
    assert store.get_challenge(task.id).status == "active"  # type: ignore[union-attr]
    assert store.list_flags(task.id) == []
    terminal = {"FLAG_CONFIRMED", "FINISH_ACCEPTED", "AGENT_FINISHED", "SESSION_STOPPED"}
    assert not terminal.intersection(event.type for event in store.list_agent_events(task.id))
    store.close()
