from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tga.contracts import TGATask
from tests.runtime_fixtures import task as v6_task
from tga.evidence.store import EvidenceStore
from tga.evidence.artifacts import ArtifactStore
from tga.domain.knowledge import KnowledgeItem
from tga.domain.planning import GlobalPlan, Intent
from tga.domain.solver import WorkerResult
from tga.infrastructure.persistence import (
    IntentClaimConflict,
    PersistenceBundle,
    PersistenceConflict,
)
from tga.infrastructure.workspace import SolverWorkspaceService
from tga.runtime.knowledge import KnowledgePromotionService
from tga.runtime.manager import Manager
from tga.runtime.coordinator import SessionCoordinator
from tga.runtime.agents.session_runner import SolverOutcome
from tga.runtime.agents.model_loop import ModelLoop
from tga.runtime.orchestration import TaskOrchestrator
from tga.runtime.scheduling import (
    BudgetManager,
    CancellationToken,
    SolverLeaseManager,
    SolverRunCompletion,
    SolverRunPool,
    SolverScheduler,
    TaskScheduler,
    NetworkBudgetLimiter,
    ModelCallLimiter,
)
from tga.runtime.tooling.governance.approvals import SolverApprovalCoordinator


NOW = "2026-07-30T00:00:00Z"


def _task(task_id: str, *, active_workers: int = 2) -> TGATask:
    return v6_task(
        id=task_id,
        name="parallel orchestration",
        mode="ctf",
        goal="run independent investigations safely",
        execution_budget={
            "max_active_workers": active_workers,
            "max_total_solvers": 8,
            "max_tool_calls": 20,
            "max_artifacts": 20,
        },
    )


def _seed_orchestrator(tmp_path: Path, task: TGATask):
    database_path = tmp_path / task.id / "evidence.db"
    bundle = PersistenceBundle.open(database_path)
    bundle.tasks.create_task(task)
    orchestrator = TaskOrchestrator(task=task, repositories=bundle)
    state = orchestrator.bootstrap()
    return database_path, bundle, orchestrator, state


def _add_intent(orchestrator: TaskOrchestrator, supervisor_id: str, suffix: str):
    return orchestrator.create_intent(
        supervisor_solver_id=supervisor_id,
        kind="validation",
        title=f"Independent route {suffix}",
        objective=f"Validate independent route {suffix}",
    )


def test_dispatches_two_independent_workers_but_serial_mode_remains_configurable(
    tmp_path: Path,
) -> None:
    task = _task("parallel_two", active_workers=2)
    _, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    try:
        _add_intent(orchestrator, state.supervisor_solver_id, "two")

        assignments = orchestrator.dispatch_ready()

        assert len(assignments) == 2
        assert len({item.intent_id for item in assignments}) == 2
        assert orchestrator.state().max_active_workers == 2
        assert orchestrator.dispatch_ready() == ()
    finally:
        bundle.close()

    serial_task = _task("parallel_serial_compat", active_workers=1)
    _, bundle, orchestrator, state = _seed_orchestrator(tmp_path, serial_task)
    try:
        _add_intent(orchestrator, state.supervisor_solver_id, "serial")
        assert len(orchestrator.dispatch_ready()) == 1
        assert orchestrator.dispatch_ready() == ()
    finally:
        bundle.close()


def test_solver_run_pool_executes_workers_with_real_overlap(tmp_path: Path) -> None:
    task = _task("parallel_run_pool")
    database_path, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    _add_intent(orchestrator, state.supervisor_solver_id, "peer")
    assignments = orchestrator.dispatch_ready()
    assert len(assignments) == 2
    runs = tuple(bundle.orchestration.list_solver_runs(task.id))
    assert len(runs) == 2 and {run.state for run in runs} == {"queued"}
    bundle.close()

    barrier = threading.Barrier(2, timeout=5)
    simultaneously_running: list[str] = []
    lock = threading.Lock()

    def execute(run, context):
        with lock:
            simultaneously_running.append(run.id)
        barrier.wait()
        context.assert_active()
        return SolverRunCompletion(
            state="completed", result_id=f"result_{run.id}", value=run.id
        )

    pool = SolverRunPool(
        repository_factory=lambda: PersistenceBundle.open(database_path),
        owner_id="runtime_a",
        max_active_workers=2,
        lease_ttl_seconds=10,
    )
    completions = pool.run(task.id, runs, execute)

    assert len(completions) == 2
    assert len(simultaneously_running) == 2
    verified = PersistenceBundle.open(database_path)
    try:
        persisted = verified.orchestration.list_solver_runs(task.id)
        assert {run.state for run in persisted} == {"completed"}
        events = verified.events.list_agent_events(task.id)
        assert sum(event.type == "SOLVER_RUN_STARTED" for event in events) == 2
        assert sum(event.type == "SOLVER_RUN_COMPLETED" for event in events) == 2
    finally:
        verified.close()


def test_cancelled_run_context_rejects_late_side_effect_boundary(tmp_path: Path) -> None:
    task = _task("parallel_stale_context", active_workers=1)
    database_path, bundle, orchestrator, _ = _seed_orchestrator(tmp_path, task)
    assignment = orchestrator.dispatch_ready()[0]
    run = bundle.orchestration.list_solver_runs(task.id)[0]
    bundle.close()
    entered = threading.Event()
    cancelled = threading.Event()
    side_effects: list[str] = []

    def execute(_run, context):
        entered.set()
        assert cancelled.wait(timeout=5)
        context.assert_active()
        side_effects.append("late-write")
        return SolverRunCompletion(state="completed")

    holder: list[tuple[SolverRunCompletion, ...]] = []
    thread = threading.Thread(
        target=lambda: holder.append(SolverRunPool(
            repository_factory=lambda: PersistenceBundle.open(database_path),
            owner_id="runtime_stale",
            max_active_workers=1,
            lease_ttl_seconds=10,
        ).run(task.id, (run,), execute))
    )
    thread.start()
    assert entered.wait(timeout=5)
    control = PersistenceBundle.open(database_path)
    try:
        control.solvers.update_solver_status(assignment.solver_id, "cancelled")
        control.orchestration.cancel_solver_runs(
            task.id, assignment.solver_id, reason="SOLVER_CANCELLED"
        )
    finally:
        control.close()
    cancelled.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert side_effects == []
    assert holder and holder[0][0].state == "cancelled"


def test_artifact_store_rejects_write_after_execution_context_is_cancelled(
    tmp_path: Path,
) -> None:
    from tga.runtime.scheduling import SolverExecutionContext

    token = CancellationToken()
    context = SolverExecutionContext(
        run_id="run_artifact_fence",
        task_id="task_artifact_fence",
        solver_id="solver_artifact_fence",
        owner_id="runtime_artifact_fence",
        fencing_token=7,
        cancellation=token,
        _is_valid=lambda: True,
    )
    token.cancel("lease_lost")

    with pytest.raises(Exception, match="lease_lost"):
        ArtifactStore(
            tmp_path / "artifacts", execution_context=context
        ).save_text(
            task_id=context.task_id,
            intent_id=None,
            kind="file",
            text="must not be published",
        )

    assert list((tmp_path / "artifacts").iterdir()) == []


def test_manager_worker_batch_uses_parallel_solver_runs(
    tmp_path: Path, monkeypatch,
) -> None:
    task = _task("parallel_manager_batch")
    database_path, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    _add_intent(orchestrator, state.supervisor_solver_id, "peer")
    orchestrator.dispatch_ready()
    runs = tuple(bundle.orchestration.list_solver_runs(task.id))
    bundle.close()

    barrier = threading.Barrier(2, timeout=5)
    entered: list[str] = []
    contexts: list[object] = []
    lock = threading.Lock()

    class BarrierRunner:
        def __init__(self, **kwargs):
            self.task = kwargs["task"]
            self.solver_id = kwargs["solver_id"]
            contexts.append(kwargs["execution_context"])

        def run(self):
            with lock:
                entered.append(self.solver_id)
            barrier.wait()
            return SolverOutcome(
                task_id=self.task.id,
                solver_id=self.solver_id,
                status="completed",
            )

    monkeypatch.setattr("tga.runtime.manager.SolverRunner", BarrierRunner)
    manager = Manager(
        run_root=tmp_path,
        model_client=object(),
        executor=object(),
    )
    completions = manager._run_worker_batch(
        task=task,
        database_path=database_path,
        client=object(),
        runs=runs,
        max_active_workers=2,
    )

    assert len(entered) == 2
    assert {context.run_id for context in contexts} == {run.id for run in runs}
    assert all(context.fencing_token == 1 for context in contexts)
    assert {item.state for item in completions} == {"completed"}


def test_manager_worker_batch_keeps_workers_parallel_when_model_calls_are_limited(
    tmp_path: Path, monkeypatch,
) -> None:
    task = _task("parallel_manager_model_limit").model_copy(
        update={
            "execution_budget": {
                "max_active_workers": 2,
                "max_total_solvers": 8,
                "max_concurrent_model_calls": 1,
            }
        }
    )
    database_path, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    _add_intent(orchestrator, state.supervisor_solver_id, "peer")
    orchestrator.dispatch_ready()
    runs = tuple(bundle.orchestration.list_solver_runs(task.id))
    bundle.close()

    active = 0
    maximum_active = 0
    lock = threading.Lock()

    class CountingRunner:
        def __init__(self, **kwargs):
            self.task = kwargs["task"]
            self.solver_id = kwargs["solver_id"]

        def run(self):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return SolverOutcome(
                task_id=self.task.id,
                solver_id=self.solver_id,
                status="completed",
            )

    monkeypatch.setattr("tga.runtime.manager.SolverRunner", CountingRunner)
    completions = Manager(
        run_root=tmp_path,
        model_client=object(),
        executor=object(),
    )._run_worker_batch(
        task=task,
        database_path=database_path,
        client=object(),
        runs=runs,
        max_active_workers=2,
    )

    assert len(completions) == 2
    assert maximum_active == 2


def test_model_call_limiter_limits_only_provider_requests() -> None:
    limiter = ModelCallLimiter(1)
    active = 0
    maximum_active = 0
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=5)

    class Gateway:
        temperature = 0.2

        def chat_tools(self, _messages, *, tools, temperature):
            del tools, temperature
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return {"message": {"role": "assistant", "content": "ok"}}

    def call_model() -> None:
        barrier.wait()
        ModelLoop(Gateway(), limiter=limiter).run(messages=[], tools=[])

    threads = [threading.Thread(target=call_model) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum_active == 1


def test_model_loop_returns_late_response_usage_after_cancellation() -> None:
    token = CancellationToken()

    class ExecutionContext:
        def assert_active(self) -> None:
            token.raise_if_cancelled()

    class Gateway:
        temperature = 0.2

        def chat_tools(self, _messages, *, tools, temperature):
            del tools, temperature
            token.cancel("operator_cancelled")
            return {
                "message": {"role": "assistant", "content": "late"},
                "usage": {"input_tokens": 7, "output_tokens": 3},
            }

    turn = ModelLoop(Gateway(), execution_context=ExecutionContext()).run(
        messages=[], tools=[]
    )

    assert turn.response["usage"] == {"input_tokens": 7, "output_tokens": 3}


def test_worker_never_enters_manager_serial_solver_selection(tmp_path: Path) -> None:
    task = _task("parallel_worker_single_path", active_workers=1)
    _, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    try:
        assignment = orchestrator.dispatch_ready()[0]
        assert Manager._next_solver_id(
            bundle, task.id, preferred_id=state.supervisor_solver_id
        ) == state.supervisor_solver_id
        assert assignment.solver_id != state.supervisor_solver_id
    finally:
        bundle.close()


def test_parallel_turn_reservation_is_atomic(tmp_path: Path) -> None:
    task = _task("parallel_turn_reservation")
    database_path, bundle, _, state = _seed_orchestrator(tmp_path, task)
    store = EvidenceStore(database_path)
    try:
        SessionCoordinator(store).ensure_session(
            task=task, max_turns=1, supervisor_solver_id=state.supervisor_solver_id
        )
        SessionCoordinator(store).start(
            task_id=task.id, solver_id=state.supervisor_solver_id
        )
    finally:
        store.close()
        bundle.close()

    barrier = threading.Barrier(2, timeout=5)
    reservations: list[bool] = []
    lock = threading.Lock()

    def reserve() -> None:
        worker_store = EvidenceStore(database_path)
        try:
            barrier.wait()
            reserved = SessionCoordinator(worker_store).reserve_turn(task_id=task.id)
            with lock:
                reservations.append(reserved is not None)
        finally:
            worker_store.close()

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    verified = EvidenceStore(database_path)
    try:
        assert reservations.count(True) == 1
        assert reservations.count(False) == 1
        assert verified.get_session(task.id).turn_count == 1
    finally:
        verified.close()


def test_parallel_solver_runs_reserve_independent_turn_budgets(tmp_path: Path) -> None:
    task = _task("parallel_run_turn_budgets", active_workers=2)
    database_path, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    try:
        session_store = EvidenceStore(database_path)
        try:
            SessionCoordinator(session_store).ensure_session(
                task=task,
                max_turns=48,
                supervisor_solver_id=state.supervisor_solver_id,
            )
            SessionCoordinator(session_store).start(
                task_id=task.id, solver_id=state.supervisor_solver_id
            )
        finally:
            session_store.close()
        _add_intent(orchestrator, state.supervisor_solver_id, "peer")
        orchestrator.dispatch_ready()
        runs = bundle.orchestration.list_solver_runs(task.id)
        assert len(runs) == 2
        reserved = []
        for index, run in enumerate(runs):
            owner = f"owner_{index}"
            claimed = bundle.orchestration.claim_solver_run(
                run.id,
                owner,
                ttl_seconds=30,
                expected_version=run.version,
                max_active_workers=2,
            )
            assert claimed is not None
            started = bundle.orchestration.start_solver_run(
                claimed.id, owner, claimed.fencing_token
            )
            reserved.append(bundle.orchestration.reserve_solver_run_turn(
                started.id, owner, started.fencing_token
            ))

        assert [item.turn_count for item in reserved] == [1, 1]
        assert all(item.max_turns > 1 for item in reserved)
        session_store = EvidenceStore(database_path)
        try:
            assert session_store.get_session(task.id).turn_count == 2
        finally:
            session_store.close()
    finally:
        bundle.close()


def test_failed_run_reconcile_creates_one_retry_attempt(tmp_path: Path) -> None:
    task = _task("parallel_failed_reconcile", active_workers=1)
    database_path, bundle, orchestrator, _ = _seed_orchestrator(tmp_path, task)
    orchestrator.dispatch_ready()
    run = bundle.orchestration.list_solver_runs(task.id)[0]
    bundle.close()

    completions = SolverRunPool(
        repository_factory=lambda: PersistenceBundle.open(database_path),
        owner_id="runtime_failure",
        max_active_workers=1,
        lease_ttl_seconds=10,
    ).run(task.id, (run,), lambda _run, _context: (_ for _ in ()).throw(RuntimeError("boom")))
    assert completions[0].state == "failed"

    recovered = PersistenceBundle.open(database_path)
    try:
        runtime = TaskOrchestrator(task=task, repositories=recovered)
        runtime.reconcile_solver_runs()
        runtime.reconcile_solver_runs()
        assignments = [
            item for item in recovered.orchestration.list_assignments(task.id)
            if item.intent_id == run.intent_id
        ]
        assert [item.attempt for item in assignments] == [1, 2]
        assert recovered.orchestration.get_solver_run(run.id).state == "failed"
    finally:
        recovered.close()


def test_two_connections_claim_different_intents_and_same_intent_has_one_winner(
    tmp_path: Path,
) -> None:
    task = _task("parallel_claim")
    database_path = tmp_path / task.id / "evidence.db"
    seed = PersistenceBundle.open(database_path)
    seed.tasks.create_task(task)
    seed.plans.save_global_plan(GlobalPlan(
        id="plan_parallel_claim",
        task_id=task.id,
        version=1,
        status="active",
        intents=[
            Intent(
                id="intent_a", task_id=task.id, title="A", objective="A",
                status="pending", created_at=NOW, updated_at=NOW,
            ),
            Intent(
                id="intent_b", task_id=task.id, title="B", objective="B",
                status="pending", created_at=NOW, updated_at=NOW,
            ),
            Intent(
                id="intent_race", task_id=task.id, title="Race", objective="Race",
                status="pending", created_at=NOW, updated_at=NOW,
            ),
        ],
        created_at=NOW,
        updated_at=NOW,
    ))
    seed.close()

    different: list[str] = []
    barrier = threading.Barrier(2)

    def claim(intent_id: str, solver_id: str, outcomes: list[str]) -> None:
        bundle = PersistenceBundle.open(database_path)
        try:
            barrier.wait(timeout=3)
            outcomes.append(bundle.plans.claim_pending_intent(
                intent_id, solver_id, expected_version=1
            ).id)
        finally:
            bundle.close()

    threads = [
        threading.Thread(target=claim, args=("intent_a", "solver_a", different)),
        threading.Thread(target=claim, args=("intent_b", "solver_b", different)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert set(different) == {"intent_a", "intent_b"}

    winners: list[str] = []
    losers: list[type[BaseException]] = []
    barrier = threading.Barrier(2)

    def race(solver_id: str) -> None:
        try:
            claim("intent_race", solver_id, winners)
        except BaseException as exc:  # assertion records the cross-thread outcome
            losers.append(type(exc))

    threads = [threading.Thread(target=race, args=(name,)) for name in ("solver_c", "solver_d")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert winners == ["intent_race"]
    assert losers == [IntentClaimConflict]


def test_solver_lease_uses_fencing_and_recovers_only_after_expiry(tmp_path: Path) -> None:
    task = _task("parallel_lease")
    database_path, bundle, orchestrator, _ = _seed_orchestrator(tmp_path, task)
    assignment = orchestrator.dispatch_ready()[0]
    bundle.close()
    first_bundle = PersistenceBundle.open(database_path)
    second_bundle = PersistenceBundle.open(database_path)
    try:
        leases_a = SolverLeaseManager(first_bundle.solvers)
        leases_b = SolverLeaseManager(second_bundle.solvers)
        start = datetime(2026, 7, 30, tzinfo=UTC)
        first = leases_a.acquire(
            task.id, assignment.solver_id, "runner_a", ttl_seconds=10, now=start
        )
        assert first is not None
        assert leases_b.acquire(
            task.id, assignment.solver_id, "runner_b", ttl_seconds=10, now=start
        ) is None
        assert leases_a.is_valid(first, now=start + timedelta(seconds=9))

        second = leases_b.acquire(
            task.id,
            assignment.solver_id,
            "runner_b",
            ttl_seconds=10,
            now=start + timedelta(seconds=10),
        )
        assert second is not None
        assert second.fencing_token > first.fencing_token
        assert not leases_a.is_valid(first, now=start + timedelta(seconds=10))
        assert leases_a.release(first) is False
        assert leases_b.release(second) is True
        assert leases_b.release(second) is False
    finally:
        first_bundle.close()
        second_bundle.close()


def test_plan_cas_conflict_reloads_and_merges_without_lost_intent(tmp_path: Path) -> None:
    task = _task("parallel_plan")
    database_path, first_bundle, first, state = _seed_orchestrator(tmp_path, task)
    second_bundle = PersistenceBundle.open(database_path)
    second = TaskOrchestrator(task=task, repositories=second_bundle)
    original = first_bundle.plans.compare_and_swap_global_plan
    injected = False

    def inject_competing_update(plan, *, expected_version):
        nonlocal injected
        if not injected:
            injected = True
            _add_intent(second, state.supervisor_solver_id, "competing")
        return original(plan, expected_version=expected_version)

    first_bundle.plans.compare_and_swap_global_plan = inject_competing_update
    try:
        _add_intent(first, state.supervisor_solver_id, "original")
        plan = first_bundle.plans.get_global_plan(task.id)
        titles = {item.title for item in plan.intents}
        assert {"Independent route competing", "Independent route original"}.issubset(titles)
        updates = [
            item for item in first_bundle.events.list_agent_events(task.id, limit=1000)
            if item.type == "PLAN_UPDATED"
        ]
        assert updates
        assert all(item.payload["new_version"] == item.payload["old_version"] + 1 for item in updates)
    finally:
        first_bundle.close()
        second_bundle.close()


def test_supervisor_plan_update_does_not_overwrite_concurrent_intent_claim(
    tmp_path: Path,
) -> None:
    task = _task("parallel_plan_claim")
    database_path, first_bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    competing = PersistenceBundle.open(database_path)
    original = first_bundle.plans.compare_and_swap_global_plan
    injected = False

    def inject_claim(plan, *, expected_version):
        nonlocal injected
        if not injected:
            injected = True
            competing.plans.claim_pending_intent(
                f"intent_initial_{task.id}",
                state.supervisor_solver_id,
                expected_version=1,
            )
        return original(plan, expected_version=expected_version)

    first_bundle.plans.compare_and_swap_global_plan = inject_claim
    try:
        _add_intent(orchestrator, state.supervisor_solver_id, "after-claim")
        initial = next(
            item
            for item in first_bundle.plans.get_global_plan(task.id).intents
            if item.id == f"intent_initial_{task.id}"
        )
        assert initial.status == "running"
        assert initial.assigned_solver_id == state.supervisor_solver_id
    finally:
        first_bundle.close()
        competing.close()


def test_solver_workspaces_isolate_parallel_writes_and_publish_immutable_artifacts(
    tmp_path: Path,
) -> None:
    service = SolverWorkspaceService(tmp_path / "workspace_task")
    left = service.for_solver("solver_left")
    right = service.for_solver("solver_right")

    left_path = left.write_text("scratch/result.txt", "left")
    right_path = right.write_text("scratch/result.txt", "right")
    assert left_path != right_path
    assert left_path.read_text(encoding="utf-8") == "left"
    assert right_path.read_text(encoding="utf-8") == "right"
    with pytest.raises(PermissionError):
        left.write_text("../solver_right/scratch/result.txt", "overwrite")

    first = service.publish_artifact(
        task_id="workspace_task", solver_id="solver_left", intent_id="intent_left",
        data=b"immutable", suffix=".txt",
    )
    replay = service.publish_artifact(
        task_id="workspace_task", solver_id="solver_right", intent_id="intent_right",
        data=b"immutable", suffix=".txt",
    )
    assert first.id == replay.id
    assert (service.shared_artifacts / first.path).read_bytes() == b"immutable"


def test_knowledge_conflict_is_queued_instead_of_last_write_winning(tmp_path: Path) -> None:
    task = _task("parallel_knowledge")
    bundle = PersistenceBundle.open(tmp_path / task.id / "evidence.db")
    bundle.tasks.create_task(task)
    try:
        for item_id, solver_id, value in (
            ("knowledge_open", "solver_a", "open"),
            ("knowledge_closed", "solver_b", "closed"),
        ):
            bundle.knowledge.add_knowledge(KnowledgeItem(
                id=item_id,
                task_id=task.id,
                scope="solver",
                target_id=solver_id,
                status="candidate",
                kind="fact",
                subject="service.port.443.state",
                value=value,
                content=f"Port 443 is {value}.",
                created_by_solver_id=solver_id,
                created_at=NOW,
            ))

        proposal = KnowledgePromotionService(bundle).request_task_promotion(
            knowledge_item_id="knowledge_open",
            proposed_by_solver_id="solver_a",
            rationale="share the observed port state",
        )

        assert proposal.status == "pending"
        conflicts = bundle.knowledge.list_conflicts(task.id, status="open")
        assert len(conflicts) == 1
        assert set(conflicts[0].knowledge_item_ids) == {
            "knowledge_open", "knowledge_closed",
        }
        assert all(item.scope == "solver" for item in bundle.knowledge.list_knowledge(task.id))
    finally:
        bundle.close()


def test_solver_scoped_approval_does_not_pause_other_worker_or_task(tmp_path: Path) -> None:
    task = _task("parallel_approval", active_workers=2)
    _, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    try:
        _add_intent(orchestrator, state.supervisor_solver_id, "approval-peer")
        first, second = orchestrator.dispatch_ready()
        SolverApprovalCoordinator(bundle.database).await_approval(
            solver_id=first.solver_id, intent_id=first.intent_id
        )

        assert bundle.solvers.get_solver(first.solver_id).status == "awaiting_approval"
        assert bundle.solvers.get_solver(second.solver_id).status == "queued"
        assert orchestrator.state().status == "running"
    finally:
        bundle.close()


def test_approval_releases_run_lease_and_requeues_fenced_continuation(
    tmp_path: Path,
) -> None:
    task = _task("parallel_approval_run")
    _, bundle, orchestrator, _ = _seed_orchestrator(tmp_path, task)
    try:
        assignment = orchestrator.dispatch_ready()[0]
        run = bundle.orchestration.list_solver_runs(task.id)[0]
        leased = bundle.orchestration.claim_solver_run(
            run.id, "runner_a", ttl_seconds=60, expected_version=run.version
        )
        assert leased is not None
        bundle.orchestration.start_solver_run(
            run.id, "runner_a", leased.fencing_token
        )
        SolverApprovalCoordinator(bundle.database).await_approval(
            solver_id=assignment.solver_id, intent_id=assignment.intent_id
        )
        waiting = bundle.orchestration.suspend_solver_run_for_approval(
            run.id, "runner_a", leased.fencing_token
        )
        assert waiting.state == "waiting_approval"
        assert waiting.lease_owner is None and waiting.lease_expires_at is None

        SolverApprovalCoordinator(bundle.database).resolve(
            solver_id=assignment.solver_id, intent_id=assignment.intent_id
        )
        resumed = bundle.orchestration.get_solver_run(run.id)
        assert resumed is not None and resumed.state == "retry_queued"
        assert resumed.fencing_token == leased.fencing_token
        reclaimed = bundle.orchestration.claim_solver_run(
            run.id, "runner_b", ttl_seconds=60, expected_version=resumed.version
        )
        assert reclaimed is not None
        assert reclaimed.fencing_token == leased.fencing_token + 1
    finally:
        bundle.close()


def test_task_token_budget_is_shared_across_parallel_solvers(tmp_path: Path) -> None:
    task = _task("parallel_budget").model_copy(update={
        "execution_budget": {
            "max_active_workers": 2,
            "max_total_solvers": 8,
            "max_total_tokens": 10,
        }
    })
    _, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    try:
        _add_intent(orchestrator, state.supervisor_solver_id, "budget-peer")
        first, second = orchestrator.dispatch_ready()
        budgets = BudgetManager(bundle.tool_governance)
        budgets.record_usage(
            idempotency_key="usage_first",
            task_id=task.id,
            solver_id=first.solver_id,
            intent_id=first.intent_id,
            input_tokens=3,
            output_tokens=3,
        )
        with pytest.raises(PersistenceConflict, match="TaskBudget total-token"):
            budgets.record_usage(
                idempotency_key="usage_second",
                task_id=task.id,
                solver_id=second.solver_id,
                intent_id=second.intent_id,
                input_tokens=3,
                output_tokens=2,
            )
    finally:
        bundle.close()


def test_model_token_reservation_blocks_parallel_request_before_provider_call(
    tmp_path: Path,
) -> None:
    task = _task("parallel_model_token_reservation").model_copy(update={
        "execution_budget": {
            "max_active_workers": 2,
            "max_total_solvers": 8,
            "max_output_tokens": 100,
        }
    })
    database_path, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    _add_intent(orchestrator, state.supervisor_solver_id, "peer")
    assignments = orchestrator.dispatch_ready()
    assert len(assignments) == 2
    bundle.close()

    first = PersistenceBundle.open(database_path)
    second = PersistenceBundle.open(database_path)
    try:
        held = first.tool_governance.reserve_model_tokens(
            idempotency_key="model-reservation-first",
            task_id=task.id,
            solver_id=assignments[0].solver_id,
            intent_id=assignments[0].intent_id,
            run_id="run_first",
            estimated_input_tokens=0,
            estimated_output_tokens=80,
        )
        with pytest.raises(PersistenceConflict, match="output-token"):
            second.tool_governance.reserve_model_tokens(
                idempotency_key="model-reservation-second",
                task_id=task.id,
                solver_id=assignments[1].solver_id,
                intent_id=assignments[1].intent_id,
                run_id="run_second",
                estimated_input_tokens=0,
                estimated_output_tokens=80,
            )
        assert first.tool_governance.release_model_tokens(held["id"]) is True
        replacement = second.tool_governance.reserve_model_tokens(
            idempotency_key="model-reservation-second",
            task_id=task.id,
            solver_id=assignments[1].solver_id,
            intent_id=assignments[1].intent_id,
            run_id="run_second",
            estimated_input_tokens=0,
            estimated_output_tokens=80,
        )
        settled = second.tool_governance.settle_model_tokens(
            replacement["id"],
            actual_input_tokens=0,
            actual_output_tokens=55,
            usage_idempotency_key="model-usage-second",
        )
        assert settled["status"] == "settled"
        assert settled["actual_output_tokens"] == 55
    finally:
        first.close()
        second.close()


def test_model_token_settlement_records_actual_overspend_and_blocks_next_request(
    tmp_path: Path,
) -> None:
    task = _task("parallel_model_token_actual_overspend").model_copy(update={
        "execution_budget": {
            "max_active_workers": 1,
            "max_total_solvers": 8,
            "max_output_tokens": 100,
        }
    })
    _, bundle, orchestrator, _ = _seed_orchestrator(tmp_path, task)
    try:
        assignment = orchestrator.dispatch_ready()[0]
        held = bundle.tool_governance.reserve_model_tokens(
            idempotency_key="model-reservation-overspend",
            task_id=task.id,
            solver_id=assignment.solver_id,
            intent_id=assignment.intent_id,
            run_id="run_overspend",
            estimated_input_tokens=0,
            estimated_output_tokens=80,
        )

        settled = bundle.tool_governance.settle_model_tokens(
            held["id"],
            actual_input_tokens=0,
            actual_output_tokens=110,
            usage_idempotency_key="model-usage-overspend",
        )

        assert settled["status"] == "settled"
        assert settled["actual_output_tokens"] == 110
        assert settled["limit_exceeded"] is True
        usage = bundle.database.conn.execute(
            "SELECT output_tokens FROM runtime_budget_usage WHERE idempotency_key=?",
            ("model-usage-overspend",),
        ).fetchone()
        assert usage["output_tokens"] == 110
        with pytest.raises(PersistenceConflict, match="output-token"):
            bundle.tool_governance.reserve_model_tokens(
                idempotency_key="model-reservation-after-overspend",
                task_id=task.id,
                solver_id=assignment.solver_id,
                intent_id=assignment.intent_id,
                run_id="run_after_overspend",
                estimated_input_tokens=0,
                estimated_output_tokens=1,
            )
    finally:
        bundle.close()


def test_cancellation_invalidates_runner_lease_and_rejects_late_worker_result(
    tmp_path: Path,
) -> None:
    task = _task("parallel_cancel")
    _, bundle, orchestrator, _ = _seed_orchestrator(tmp_path, task)
    try:
        assignment = orchestrator.dispatch_ready()[0]
        lease = SolverLeaseManager(bundle.solvers).acquire(
            task.id, assignment.solver_id, "runner", ttl_seconds=60
        )
        assert lease is not None
        token = CancellationToken()
        token.cancel("task_cancelled")
        orchestrator.cancel(reason="concurrent_cancel")

        assert not SolverLeaseManager(bundle.solvers).is_valid(lease)
        cancelled_run = bundle.orchestration.list_solver_runs(task.id)[0]
        assert cancelled_run.state == "cancelled"
        assert cancelled_run.error_code == "TASK_CANCELLED"
        with pytest.raises(PersistenceConflict, match="lease|cancel",):
            orchestrator.submit_worker_result(
                WorkerResult(
                    task_id=task.id,
                    solver_id=assignment.solver_id,
                    intent_id=assignment.intent_id,
                    status="succeeded",
                    summary="late result",
                ),
                lease=lease,
            )
    finally:
        bundle.close()


def test_failed_intent_retry_uses_new_solver_and_attempt(tmp_path: Path) -> None:
    task = _task("parallel_retry")
    _, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    try:
        first = orchestrator.dispatch_ready()[0]
        orchestrator.submit_worker_result(WorkerResult(
            task_id=task.id,
            solver_id=first.solver_id,
            intent_id=first.intent_id,
            status="failed",
            summary="first attempt failed",
        ))

        second = orchestrator.retry_intent(
            supervisor_solver_id=state.supervisor_solver_id,
            intent_id=first.intent_id,
        )

        assert second.attempt == 2
        assert second.solver_id != first.solver_id
        assert second.solver_id.endswith("_a2")
    finally:
        bundle.close()


def test_expired_solver_run_recovers_as_new_fenced_attempt(tmp_path: Path) -> None:
    task = _task("parallel_run_recovery")
    _, bundle, orchestrator, _ = _seed_orchestrator(tmp_path, task)
    try:
        first = orchestrator.dispatch_ready()[0]
        run = bundle.orchestration.list_solver_runs(task.id)[0]
        start = datetime(2026, 7, 30, tzinfo=UTC)
        leased = bundle.orchestration.claim_solver_run(
            run.id, "crashed_runner", ttl_seconds=5,
            expected_version=run.version, now=start,
        )
        assert leased is not None
        bundle.orchestration.start_solver_run(
            run.id, "crashed_runner", leased.fencing_token,
            now=start + timedelta(seconds=1),
        )

        orchestrator.recover()

        runs = bundle.orchestration.list_solver_runs(task.id)
        assert len(runs) == 2
        expired = next(item for item in runs if item.id == run.id)
        retry = next(item for item in runs if item.id != run.id)
        assert expired.state == "expired"
        assert retry.state == "queued" and retry.attempt == 2
        assert retry.solver_id != first.solver_id
    finally:
        bundle.close()


def test_task_and_solver_schedulers_use_task_solver_units_and_distinct_leases(
    tmp_path: Path,
) -> None:
    task = _task("parallel_scheduler")
    database_path, bundle, orchestrator, _ = _seed_orchestrator(tmp_path, task)
    assignment = orchestrator.dispatch_ready()[0]
    bundle.close()
    factory = lambda: PersistenceBundle.open(database_path)

    task_scheduler = TaskScheduler(repository_factory=factory, owner_id="orchestrator_a")
    nested_results: list[bool] = []

    def orchestrate(_context) -> None:
        competing = TaskScheduler(
            repository_factory=factory, owner_id="orchestrator_b"
        )
        nested_results.append(competing.run_once(task.id, lambda _ctx: None))

    assert task_scheduler.run_once(task.id, orchestrate) is True
    assert nested_results == [False]

    solver_scheduler = SolverScheduler(
        repository_factory=factory, owner_id="runner_a", max_active_workers=2
    )
    competing_results: list[bool] = []

    def run_solver(_context) -> None:
        competing = SolverScheduler(
            repository_factory=factory, owner_id="runner_b", max_active_workers=2
        )
        competing_results.append(competing.run_once(
            task.id, assignment.solver_id, lambda _ctx: None
        ))

    assert solver_scheduler.run_once(task.id, assignment.solver_id, run_solver) is True
    assert competing_results == [False]


def test_task_orchestrator_state_rejects_stale_cas_writer(tmp_path: Path) -> None:
    task = _task("parallel_state_cas")
    database_path, first_bundle, _orchestrator, _ = _seed_orchestrator(tmp_path, task)
    second_bundle = PersistenceBundle.open(database_path)
    try:
        first = first_bundle.orchestration.get_state(task.id)
        stale = second_bundle.orchestration.get_state(task.id)
        assert first is not None and stale is not None
        persisted = first_bundle.orchestration.save_state(
            first.model_copy(update={"status": "paused"})
        )

        assert persisted.version == first.version + 1
        with pytest.raises(PersistenceConflict, match="changed concurrently"):
            second_bundle.orchestration.save_state(
                stale.model_copy(update={"status": "blocked"})
            )
    finally:
        second_bundle.close()
        first_bundle.close()


def test_task_network_budget_enforces_concurrency_and_sliding_rate(tmp_path: Path) -> None:
    task = _task("parallel_network_budget").model_copy(update={
        "execution_budget": {
            "max_active_workers": 2,
            "max_total_solvers": 8,
            "max_network_concurrency": 1,
            "max_network_requests_per_minute": 2,
            "max_network_requests": 2,
        }
    })
    _, bundle, orchestrator, _ = _seed_orchestrator(tmp_path, task)
    try:
        assignment = orchestrator.dispatch_ready()[0]
        limiter = NetworkBudgetLimiter(bundle.tool_governance)
        first = limiter.acquire(
            idempotency_key="network_first",
            task_id=task.id,
            solver_id=assignment.solver_id,
            intent_id=assignment.intent_id,
        )
        with pytest.raises(PersistenceConflict, match="concurrency"):
            limiter.acquire(
                idempotency_key="network_blocked",
                task_id=task.id,
                solver_id=assignment.solver_id,
                intent_id=assignment.intent_id,
            )

        assert limiter.release(first) is True
        assert limiter.release(first) is False
        second = limiter.acquire(
            idempotency_key="network_second",
            task_id=task.id,
            solver_id=assignment.solver_id,
            intent_id=assignment.intent_id,
        )
        limiter.release(second)
        with pytest.raises(PersistenceConflict, match="rate|network-requests"):
            limiter.acquire(
                idempotency_key="network_third",
                task_id=task.id,
                solver_id=assignment.solver_id,
                intent_id=assignment.intent_id,
            )
    finally:
        bundle.close()


def test_shared_artifact_bytes_are_hard_limited_across_solvers(tmp_path: Path) -> None:
    task = _task("parallel_artifact_budget").model_copy(update={
        "execution_budget": {
            "max_active_workers": 2,
            "max_total_solvers": 8,
            "max_artifact_bytes": 8,
        }
    })
    _, bundle, orchestrator, state = _seed_orchestrator(tmp_path, task)
    try:
        _add_intent(orchestrator, state.supervisor_solver_id, "artifact-peer")
        first, second = orchestrator.dispatch_ready()
        service = SolverWorkspaceService(
            tmp_path / task.id,
            budget_manager=BudgetManager(bundle.tool_governance),
        )
        service.publish_artifact(
            task_id=task.id,
            solver_id=first.solver_id,
            intent_id=first.intent_id,
            data=b"12345",
        )
        with pytest.raises(PersistenceConflict, match="artifact-bytes"):
            service.publish_artifact(
                task_id=task.id,
                solver_id=second.solver_id,
                intent_id=second.intent_id,
                data=b"6789",
            )
        assert len(tuple(service.shared_artifacts.iterdir())) == 1
    finally:
        bundle.close()
