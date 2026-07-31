from __future__ import annotations

import threading
from pathlib import Path

from datetime import UTC, datetime, timedelta

from tga.contracts import ModelSnapshot, SessionRecord, TGATask
from tga.domain.governance.models import ActionEffect
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.coordinator import SessionCoordinator
from tga.runtime.errors import RuntimeConfigurationError
from tga.runtime.manager import Manager
from tga.runtime.orchestration import TaskOrchestrator
from tga.runtime.scheduler import RuntimeScheduler
from tga.runtime.tooling.requests import ActionContext, ApprovalRequest, AuthorizationDecision, GovernedAction


def _seed(tmp_path: Path, task_id: str) -> EvidenceStore:
    root = tmp_path / task_id
    store = EvidenceStore(root / "evidence.db")
    task = TGATask(
        id=task_id, name=task_id, mode="ctf", goal="test", schema_version=6
    )
    store.create_task(task)
    state = TaskOrchestrator(
        task=task, repositories=PersistenceBundle(store)
    ).bootstrap()
    assert state.supervisor_solver_id is not None
    store.create_session(SessionRecord(
        task_id=task_id, status="created", schema_version=6,
        active_solver_id=state.supervisor_solver_id,
    ))
    return store


def _pending_approval(store: EvidenceStore, task_id: str, action_id: str, deadline: str) -> GovernedAction:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    solver_id = store.get_session(task_id).active_solver_id
    assert solver_id is not None
    action = GovernedAction(
        id=action_id,
        context=ActionContext(
            task_id=task_id,
            solver_id=solver_id,
            orchestration_role="supervisor",
            solver_definition_id="task-supervisor",
            execution_policy_snapshot_id="execution:" + "a" * 64,
            solver_tool_policy_snapshot_id="tool:" + "b" * 64,
            created_at=now,
        ),
        provider_tool_name="fixture_delete",
        tool_call_id=f"call_{action_id}",
        tool_class="execution",
        capability="fixture.delete",
        normalized_arguments={},
        resolved_target="fixture.delete",
        rationale="test approval scheduling",
        risk="destructive",
        effect=ActionEffect(scope="target", persistence="persistent", description="delete fixture"),
        authorization=AuthorizationDecision(
            allowed=True,
            code="APPROVAL_REQUIRED",
            reason="approval required",
            requires_approval=True,
        ),
        status="pending_approval",
        created_at=now,
        updated_at=now,
    )
    repository = PersistenceBundle(store).tool_governance
    repository.add_action(action)
    repository.save_approval(ApprovalRequest(
        id=f"approval_{action_id}",
        task_id=task_id,
        solver_id=solver_id,
        action_id=action_id,
        governed_action_id=action_id,
        reason="approval required",
        risk="destructive",
        effect=action.effect,
        expires_at=deadline,
        created_at=now,
        updated_at=now,
    ))
    return action


def test_scheduler_deduplicates_running_task_and_releases_slot(tmp_path: Path) -> None:
    store = _seed(tmp_path, "task")
    store.close()
    entered = threading.Event()
    release = threading.Event()

    def run(_task_id: str) -> None:
        entered.set()
        release.wait(timeout=2)

    scheduler = RuntimeScheduler(run_root=tmp_path, run_task=run)
    assert scheduler.schedule("task") is True
    assert entered.wait(timeout=1)
    assert scheduler.schedule("task") is False
    release.set()
    for _ in range(100):
        if not scheduler.is_running("task"):
            break
        threading.Event().wait(0.01)
    assert scheduler.is_running("task") is False


def test_scheduler_lease_deduplicates_across_instances(tmp_path: Path) -> None:
    store = _seed(tmp_path, "shared")
    store.close()
    entered = threading.Event()
    release = threading.Event()

    def run(_task_id: str) -> None:
        entered.set()
        release.wait(timeout=2)

    first = RuntimeScheduler(run_root=tmp_path, run_task=run)
    second = RuntimeScheduler(run_root=tmp_path, run_task=run)
    assert first.schedule("shared") is True
    assert entered.wait(timeout=1)
    assert second.schedule("shared") is False
    release.set()


def test_scheduler_replays_request_made_while_runner_is_releasing(tmp_path: Path) -> None:
    store = _seed(tmp_path, "handoff")
    store.close()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    calls: list[str] = []

    def run(task_id: str) -> None:
        calls.append(task_id)
        if len(calls) == 1:
            first_entered.set()
            release_first.wait(timeout=2)
        else:
            second_entered.set()

    scheduler = RuntimeScheduler(run_root=tmp_path, run_task=run)
    assert scheduler.schedule("handoff") is True
    assert first_entered.wait(timeout=1)
    assert scheduler.schedule("handoff") is False
    release_first.set()
    assert second_entered.wait(timeout=2)
    assert calls == ["handoff", "handoff"]


def test_scheduler_recover_rearms_approval_without_scheduling_runnable_sessions(tmp_path: Path) -> None:
    store = _seed(tmp_path, "approval_restart")
    deadline = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    action = _pending_approval(store, "approval_restart", "governed_restart", deadline)
    solver_id = store.get_session("approval_restart").active_solver_id
    SessionCoordinator(store).start(task_id="approval_restart", solver_id=solver_id)
    SessionCoordinator(store).await_approval(task_id="approval_restart", action_id=action.id)
    store.close()

    scheduler = RuntimeScheduler(run_root=tmp_path, run_task=lambda _task_id: None)
    assert scheduler.recover(schedule_runnable=False) == []
    assert scheduler._approval_timers["approval_restart"][:2] == (action.id, deadline)
    scheduler._approval_timers["approval_restart"][2].cancel()


def test_new_approval_replaces_previous_action_timer(tmp_path: Path) -> None:
    store = _seed(tmp_path, "approval_replace")
    coordinator = SessionCoordinator(store)
    solver_id = store.get_session("approval_replace").active_solver_id
    coordinator.start(task_id="approval_replace", solver_id=solver_id)
    first_deadline = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    first = _pending_approval(store, "approval_replace", "governed_first", first_deadline)
    coordinator.await_approval(task_id="approval_replace", action_id=first.id)
    store.close()
    scheduler = RuntimeScheduler(run_root=tmp_path, run_task=lambda _task_id: None)
    scheduler._arm_approval_expiry("approval_replace")
    old_timer = scheduler._approval_timers["approval_replace"][2]

    store = EvidenceStore(tmp_path / "approval_replace" / "evidence.db")
    PersistenceBundle(store).tool_governance.transition(
        first.id, "rejected", expected_status="pending_approval"
    )
    coordinator = SessionCoordinator(store)
    coordinator.resume(task_id="approval_replace", reason="test_first_resolved")
    second_deadline = (datetime.now(UTC) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    second = _pending_approval(store, "approval_replace", "governed_second", second_deadline)
    coordinator.await_approval(task_id="approval_replace", action_id=second.id)
    store.close()

    scheduler._arm_approval_expiry("approval_replace")
    assert scheduler._approval_timers["approval_replace"][:2] == (second.id, second_deadline)
    assert not old_timer.is_alive()
    scheduler._approval_timers["approval_replace"][2].cancel()


def test_scheduler_persists_redacted_background_failure(tmp_path: Path) -> None:
    task_id = "scheduler_failure"
    store = _seed(tmp_path, task_id)
    store.close()
    finished = threading.Event()

    def fail(_task_id: str) -> None:
        try:
            raise RuntimeError("Authorization: Bearer top-secret-provider-token")
        finally:
            finished.set()

    scheduler = RuntimeScheduler(run_root=tmp_path, run_task=fail)
    assert scheduler.schedule(task_id) is True
    assert finished.wait(timeout=1)
    for _ in range(100):
        if not scheduler.is_running(task_id):
            break
        threading.Event().wait(0.01)

    store = EvidenceStore(tmp_path / task_id / "evidence.db")
    try:
        session = store.get_session(task_id)
        solver = PersistenceBundle(store).solvers.list_solvers(task_id)[0]
        stopped = [event for event in store.list_agent_events(task_id) if event.type == "SESSION_STOPPED"][-1]
        assert session is not None and session.status == "failed"
        assert session.stop_reason == "background_runtime_failed"
        assert solver.status == "failed"
        assert stopped.payload["error"]["code"] == "BACKGROUND_RUNTIME_FAILED"
        assert "top-secret-provider-token" not in stopped.payload["error"]["message"]
        assert "[REDACTED]" in stopped.payload["error"]["message"]
    finally:
        store.close()


def test_scheduler_blocks_retryable_runtime_configuration_failure(tmp_path: Path) -> None:
    task_id = "scheduler_configuration_blocked"
    store = _seed(tmp_path, task_id)
    store.close()
    finished = threading.Event()

    def fail(_task_id: str) -> None:
        try:
            raise RuntimeConfigurationError(
                code="MODEL_CONFIGURATION_STALE",
                message="runtime model verification is not current",
            )
        finally:
            finished.set()

    scheduler = RuntimeScheduler(run_root=tmp_path, run_task=fail)
    assert scheduler.schedule(task_id) is True
    assert finished.wait(timeout=1)
    for _ in range(100):
        if not scheduler.is_running(task_id):
            break
        threading.Event().wait(0.01)

    store = EvidenceStore(tmp_path / task_id / "evidence.db")
    try:
        session = store.get_session(task_id)
        solver = PersistenceBundle(store).solvers.list_solvers(task_id)[0]
        stopped = [event for event in store.list_agent_events(task_id) if event.type == "SESSION_STOPPED"][-1]
        assert session is not None and session.status == "blocked"
        assert session.stop_reason == "runtime_configuration_blocked"
        assert solver.status == "blocked"
        assert stopped.payload["error"] == {
            "code": "MODEL_CONFIGURATION_STALE",
            "message": "runtime model verification is not current",
            "phase": "provider",
            "retryable": True,
        }
    finally:
        store.close()


def test_manager_classifies_unavailable_provider_credentials(monkeypatch) -> None:
    task = TGATask(
        id="provider_credentials", name="provider credentials", mode="ctf", goal="test",
        model_snapshot=ModelSnapshot(
            model="test-model", capability_fingerprint="a" * 64,
            verification_id="verify_test", verified_at="2026-07-24T00:00:00Z",
            max_output_tokens=1024, timeout_seconds=30, temperature=0,
        ),
    )
    monkeypatch.setattr(
        "tga.runtime.manager.model_config_status",
        lambda: {"configured": False, "verification_status": "unverified", "verification": {}},
    )

    try:
        Manager._require_model_snapshot(task)
    except RuntimeConfigurationError as exc:
        assert exc.code == "CREDENTIAL_UNAVAILABLE"
        assert exc.retryable is True
    else:
        raise AssertionError("unavailable provider credentials were accepted")


def test_manager_classifies_stale_model_snapshot(monkeypatch) -> None:
    task = TGATask(
        id="provider_stale", name="provider stale", mode="ctf", goal="test",
        model_snapshot=ModelSnapshot(
            model="test-model", capability_fingerprint="a" * 64,
            verification_id="verify_test", verified_at="2026-07-24T00:00:00Z",
            max_output_tokens=1024, timeout_seconds=30, temperature=0,
        ),
    )
    monkeypatch.setattr(
        "tga.runtime.manager.model_config_status",
        lambda: {
            "configured": True, "verification_status": "verified",
            "verification": {"capability_fingerprint": "b" * 64},
        },
    )

    try:
        Manager._require_model_snapshot(task)
    except RuntimeConfigurationError as exc:
        assert exc.code == "MODEL_CONFIGURATION_STALE"
        assert exc.retryable is True
    else:
        raise AssertionError("stale model snapshot was accepted")
