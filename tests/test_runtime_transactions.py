from __future__ import annotations

from pathlib import Path

import pytest

from tga.capabilities.registry import build_default_registry
from tga.contracts import ActionResult, ActionSpec, ArtifactRecord, SessionRecord, SolverRecord, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore, utc_now
from tga.runtime.coordinator import SessionCoordinator, SessionTransitionError
from tga.runtime.handlers import ActionRecorder, HandlerState
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.tools.mcp_manager import MCPManager


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


def test_nested_transaction_exception_marks_outer_transaction_rollback_only(tmp_path: Path) -> None:
    task = _task("nested_rollback")
    store = _store(tmp_path, task)
    with store.transaction():
        store.add_event(task.id, "outer", {"value": 1})
        try:
            with store.transaction():
                store.add_event(task.id, "inner", {"value": 2})
                raise RuntimeError("inject nested failure")
        except RuntimeError:
            pass
        store.add_event(task.id, "outer_after", {"value": 3})

    assert store.task_snapshot(task.id)["events"] == []
    store.close()


def test_running_transition_updates_session_solver_challenge_and_events_atomically(tmp_path: Path) -> None:
    task = _task("atomic_start")
    store = _store(tmp_path, task)
    coordinator = SessionCoordinator(store)
    _, solver_id = coordinator.ensure_runtime(task=task, max_turns=4)

    session = coordinator.start(task_id=task.id, solver_id=solver_id)

    assert session.status == "running"
    assert store.list_solvers(task.id)[0].status == "running"
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


def test_running_session_restart_recovers_solver_in_same_transaction(tmp_path: Path) -> None:
    task = _task("restart_solver")
    store = _store(tmp_path, task)
    coordinator = SessionCoordinator(store)
    coordinator.create(
        SessionRecord(task_id=task.id, status="running", active_solver_id="solver_main", max_turns=4)
    )
    store.add_solver(SolverRecord(id="solver_main", task_id=task.id, status="waiting"))

    session = coordinator.start(task_id=task.id, solver_id="solver_main")

    assert session.status == "running"
    assert store.list_solvers(task.id)[0].status == "running"
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


def test_action_result_failure_rolls_back_action_and_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task("action_rollback")
    store = _store(tmp_path, task)
    action = ActionSpec(
        id="act_failure",
        task_id=task.id,
        solver_id="solver_main",
        kind="workspace",
        capability="workspace.read",
        target="input.txt",
        arguments={"relative_path": "input.txt"},
        rationale="read task evidence",
        risk="passive",
    )
    result = ActionResult(
        action_id=action.id,
        task_id=task.id,
        solver_id=action.solver_id,
        status="succeeded",
        summary="read succeeded",
    )

    def fail_result(_result: ActionResult) -> None:
        raise RuntimeError("inject result write failure")

    manager = MCPManager(cache_path=tmp_path / "mcp-cache.json")
    state = HandlerState(
        task=task,
        store=store,
        run_root=tmp_path,
        client=object(),
        executor=object(),
        solver_id="solver_main",
        workspace=tmp_path / "workspace",
        mcp_manager=manager,
        mcp_snapshot=manager.snapshot_for_task(task, workspace=tmp_path / "workspace"),
        registry=build_default_registry(),
        tool_by_name={},
    )
    monkeypatch.setattr(store, "add_action_result", fail_result)
    with pytest.raises(RuntimeError, match="result write failure"):
        ActionRecorder(state).block(action, result)

    assert store.list_actions(task.id) == []
    assert store.list_agent_events(task.id) == []
    state.close()
    store.close()


def test_schema_v6_action_result_is_idempotent_and_immutable(tmp_path: Path) -> None:
    task = _task("immutable_action_result")
    store = _store(tmp_path, task)
    action = ActionSpec(
        id="act_immutable",
        task_id=task.id,
        solver_id="solver_main",
        intent_id=f"intent_initial_{task.id}",
        kind="workspace",
        capability="workspace.read",
        target="input.txt",
        arguments={"relative_path": "input.txt"},
        rationale="read task evidence",
        risk="passive",
    )
    first = ActionResult(
        action_id=action.id,
        task_id=task.id,
        solver_id=action.solver_id,
        status="succeeded",
        summary="original result",
    )
    conflicting = first.model_copy(update={"summary": "different result"})
    try:
        store.add_action(action, status="running")
        store.add_action_result(first)
        store.add_action_result(first)

        with pytest.raises(PersistenceConflict, match="immutable"):
            store.add_action_result(conflicting)

        assert store.get_action_result(action.id)["summary"] == "original result"
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
    coordinator = SessionCoordinator(store)
    _, solver_id = coordinator.ensure_runtime(task=task, max_turns=4)
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
    assert store.list_solvers(task.id)[0].status == "running"
    assert store.get_challenge(task.id).status == "active"  # type: ignore[union-attr]
    assert store.task_snapshot(task.id)["flags"] == []
    terminal = {"FLAG_CONFIRMED", "FINISH_ACCEPTED", "AGENT_FINISHED", "SESSION_STOPPED"}
    assert not terminal.intersection(event.type for event in store.list_agent_events(task.id))
    store.close()
