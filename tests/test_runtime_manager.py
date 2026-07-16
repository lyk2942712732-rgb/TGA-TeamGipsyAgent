from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tga.contracts import ActionResult, ActionSpec, SolverRecord, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.runtime.board import BoardStore, HypothesisDraft
from tga.runtime.manager import Manager
from tga.runtime.observer import BoardObserver, ObserverPatch
from tga.runtime.prompts import build_solver_context
from tga.runtime.solver import MainSolver, SolverInterpretation


class FlagExecutor:
    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
        artifact = self.artifacts.save_text(
            task_id=task.id, intent_id=None, kind="stdout", text="proof: flag{runtime_real_123}",
            tool="test", target=task.target,
        )
        return ActionResult(
            action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded",
            summary="landing page proved the candidate flag", artifact_ids=[artifact.id],
            facts=["landing page was reachable"], candidate_flags=["flag{runtime_real_123}"],
        )


class FailedExecutor:
    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
        artifact = self.artifacts.save_text(
            task_id=task.id, intent_id=None, kind="stdout", text="the payload did not establish the premise",
            tool="test", target=task.target,
        )
        return ActionResult(
            action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="failed",
            summary="single payload failed; premise remains unproven", artifact_ids=[artifact.id],
        )


def _task() -> TGATask:
    return TGATask(
        id="runtime_manager", name="runtime", mode="ctf", target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"], goal="solve", flag_format=r"flag\{[^}]+\}",
    )


def test_manager_persists_session_board_events_and_gated_flag(tmp_path: Path):
    task = _task()
    run_root = tmp_path / "runs"
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    manager = Manager(
        store=store, run_root=run_root,
        executor=FlagExecutor(ArtifactStore(run_root / task.id / "artifacts")), solver=MainSolver(),
    )

    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "completed"
    assert snapshot["session"]["stop_reason"] == "confirmed_flag"
    assert snapshot["flags"][0]["value"] == "flag{runtime_real_123}"
    assert len(snapshot["board"]["hypotheses"]) == 2
    assert snapshot["board"]["memory"][0]["artifact_ids"]
    assert [event["seq"] for event in snapshot["agent_events"]] == list(range(1, len(snapshot["agent_events"]) + 1))
    assert (run_root / task.id / "session" / "checkpoint.json").is_file()


def test_manager_control_and_hint_write_through_runtime_only(tmp_path: Path):
    task = _task()
    run_root = tmp_path / "runs"
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    manager = Manager(store=store, run_root=run_root)
    from tga.runtime.session import AgentSession

    AgentSession(store=store, run_root=run_root, task_id=task.id).ensure(max_turns=4)
    paused = manager.control_session(task_id=task.id, action="pause")
    hint = manager.add_hint(task_id=task.id, content="Try the documented login form first.")

    assert paused["status"] == "paused"
    assert hint["accepted"] is True
    assert store.task_snapshot(task.id)["board"]["memory"][0]["source"] == "user"


def test_manager_pause_resume_cancel_states_are_durable(tmp_path: Path):
    task = _task()
    run_root = tmp_path / "runs"
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    manager = Manager(store=store, run_root=run_root)
    from tga.runtime.session import AgentSession

    AgentSession(store=store, run_root=run_root, task_id=task.id).ensure(max_turns=4)
    assert manager.control_session(task_id=task.id, action="pause")["status"] == "paused"
    assert manager.control_session(task_id=task.id, action="resume")["status"] == "running"
    assert manager.control_session(task_id=task.id, action="cancel")["status"] == "cancelled"
    assert store.get_session(task.id).finished_at is not None


def test_manager_start_creates_v2_session_and_persists_initial_hint(tmp_path: Path):
    task = _task()
    run_root = tmp_path / "runs"
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    manager = Manager(store=store, run_root=run_root)

    started = manager.start_session(task_id=task.id, initial_hint="Use the form contract before fuzzing.")

    assert started == {"accepted": True, "status": "created"}
    snapshot = store.task_snapshot(task.id)
    assert snapshot["session"]["status"] == "created"
    assert snapshot["board"]["memory"][0]["kind"] == "hint"
    assert [event["type"] for event in snapshot["agent_events"]] == ["USER_HINT", "MEMORY_UPSERTED", "BOARD_SNAPSHOT"]


def test_default_manager_reloads_runtime_solver_after_model_configuration_changes(tmp_path: Path, monkeypatch):
    task = _task()
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    manager = Manager(store=store, run_root=root, executor=FlagExecutor(ArtifactStore(root / task.id / "artifacts")))

    class NewlyConfiguredSolver(MainSolver):
        model_name = "configured-runtime-model"

    import tga.runtime.manager as manager_module
    monkeypatch.setattr(manager_module, "build_runtime_solver", NewlyConfiguredSolver)

    snapshot = manager.run_session(task.id)

    assert snapshot["solvers"][0]["model_name"] == "configured-runtime-model"


def test_agent_event_sequence_is_atomic_across_store_connections(tmp_path: Path):
    task = _task()
    db_path = tmp_path / "events.db"
    seed = EvidenceStore(db_path)
    seed.create_task(task)
    seed.close()

    def append(index: int) -> int:
        store = EvidenceStore(db_path)
        try:
            return store.append_agent_event(task_id=task.id, type="TEST", payload={"index": index}).seq
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(append, range(20)))
    assert sorted(values) == list(range(1, 21))


def test_manager_stalls_repeated_semantic_action_without_rejecting_hypothesis(tmp_path: Path):
    task = _task()
    run_root = tmp_path / "runs"
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    manager = Manager(
        store=store, run_root=run_root,
        executor=FailedExecutor(ArtifactStore(run_root / task.id / "artifacts")), solver=MainSolver(),
    )

    snapshot = manager.run_session(task.id)

    stalled = [event for event in snapshot["agent_events"] if event["type"] == "HYPOTHESIS_STALLED"]
    assert len(stalled) == 1
    hypothesis_id = stalled[0]["payload"]["hypothesis_id"]
    hypothesis = next(item for item in snapshot["board"]["hypotheses"] if item["id"] == hypothesis_id)
    assert hypothesis["status"] == "inconclusive"
    assert len([item for item in snapshot["actions"] if item["hypothesis_id"] == hypothesis_id]) == 3


def test_restart_recovers_durable_board_event_sequence_and_next_turn(tmp_path: Path):
    task = _task()
    run_root = tmp_path / "runs"
    db_path = run_root / task.id / "evidence.db"
    first = EvidenceStore(db_path)
    first.create_task(task)
    from tga.runtime.session import AgentSession

    AgentSession(store=first, run_root=run_root, task_id=task.id).ensure(max_turns=4)
    first.add_solver(SolverRecord(id="solver_resume", task_id=task.id, status="running", model_name="resume"))
    first.update_session(task.id, status="running", active_solver_id="solver_resume")
    BoardStore(first).create_hypothesis(
        task_id=task.id,
        owner_solver_id="solver_resume",
        draft=HypothesisDraft("A persisted landing page remains testable.", "recon", task.target, "resume", "fetch root", 0.8),
    )
    first.append_agent_event(task_id=task.id, type="SESSION_STARTED", payload={})
    first.close()

    reopened = EvidenceStore(db_path)
    snapshot = Manager(
        store=reopened, run_root=run_root,
        executor=FlagExecutor(ArtifactStore(run_root / task.id / "artifacts")), solver=MainSolver(),
    ).run_session(task.id)

    assert snapshot["session"]["turn_count"] == 1
    assert snapshot["flags"][0]["value"] == "flag{runtime_real_123}"
    assert [event["seq"] for event in snapshot["agent_events"]] == list(range(1, len(snapshot["agent_events"]) + 1))
    assert (run_root / task.id / "board" / "snapshot.json").is_file()


def test_prompt_context_is_compact_and_artifact_linked():
    task = _task()
    context = build_solver_context(
        task=task,
        snapshot={
            "session": {"turn_count": 3},
            "board": {"hypotheses": [{"id": "hyp", "status": "testing"}], "memory": [{"id": "mem", "kind": "evidence", "content": "short fact", "artifact_ids": ["artifact_1"], "source": "solver:s"}]},
            "actions": [{"id": "act", "capability": "http.request", "result": {"summary": "HTTP 200", "artifact_ids": ["artifact_1"], "raw_body": "must not leak"}}],
        },
    )
    assert context["memory"][0]["artifact_ids"] == ["artifact_1"]
    assert "raw_body" not in context["recent_actions"][0]["result"]


def test_board_compacts_memory_and_observer_patch_cannot_emit_actions(tmp_path: Path):
    task = _task()
    store = EvidenceStore(tmp_path / "evidence.db")
    store.create_task(task)
    board = BoardStore(store)
    for index in range(21):
        board.add_memory(task_id=task.id, kind="hint", content=f"user hint {index}", source="user")
    active = store.list_memory(task.id)
    assert len(active) <= 20
    assert any(item.kind == "decision" and item.source == "system" for item in active)

    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError):
        ObserverPatch.model_validate({"action_specs": [{"capability": "http.request"}]})
    patch = ObserverPatch(memory_upserts=[])
    BoardObserver.apply(board=board, task_id=task.id, patch=patch)
    assert store.list_actions(task.id) == []
    assert store.task_snapshot(task.id)["flags"] == []


def test_manager_rejects_candidate_flag_without_persisted_evidence(tmp_path: Path):
    class NoEvidenceExecutor:
        def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
            return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="failed", summary="no artifact", candidate_flags=["flag{unproven}"])

    task = _task()
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    snapshot = Manager(store=store, run_root=root, executor=NoEvidenceExecutor(), solver=MainSolver()).run_session(task.id)
    assert snapshot["flags"] == []
    assert any(event["type"] == "GATE_REJECTED" for event in snapshot["agent_events"])


def test_observer_runs_after_six_turns_and_can_only_patch_board(tmp_path: Path):
    class SixTurnSolver:
        model_name = "six-turn"

        def initial_hypotheses(self, *, task: TGATask, solver_id: str):
            return [HypothesisDraft(f"path {index} is testable", "web", f"{task.target}/{index}", "independent candidate", "perform one bounded test", 0.5) for index in range(3)]

        def propose_action(self, *, task: TGATask, solver_id: str, hypothesis, snapshot):
            return ActionSpec(id=f"action_{hypothesis.id}_{hypothesis.attempt_count}", task_id=task.id, solver_id=solver_id, hypothesis_id=hypothesis.id, kind="http", capability="http.request", target=task.target, arguments={"method": "GET", "path": f"/{hypothesis.attempt_count}"}, rationale="bounded test", risk="passive")

        def result_summary(self, *, hypothesis, result):
            return result.summary

        def interpret_result(self, *, hypothesis, result):
            return SolverInterpretation(status="inconclusive" if hypothesis.attempt_count else "testing", last_result="needs a different test")

    class ArtifactExecutor:
        def __init__(self, artifacts: ArtifactStore): self.artifacts = artifacts
        def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
            artifact = self.artifacts.save_text(task_id=task.id, intent_id=None, kind="stdout", text=f"evidence {action.id}", tool="fake", target=task.target)
            return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded", summary="bounded test complete", artifact_ids=[artifact.id])

    class RecordingObserver:
        def review(self, snapshot):
            artifact_id = snapshot["recent_actions"][-1]["result"]["artifact_ids"][0]
            return ObserverPatch(memory_upserts=[{"kind": "evidence", "content": "six-turn review", "source": "observer", "artifact_ids": [artifact_id]}], reminder="change attack class after six tests")

    task = _task()
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    snapshot = Manager(
        store=store, run_root=root, solver=SixTurnSolver(), observer=RecordingObserver(),
        executor=ArtifactExecutor(ArtifactStore(root / task.id / "artifacts")),
    ).run_session(task.id)

    assert len(snapshot["actions"]) == 6
    assert any(event["type"] == "OBSERVER_REVIEWED" for event in snapshot["agent_events"])
    assert any(item["content"] == "six-turn review" for item in snapshot["board"]["memory"])
    assert snapshot["flags"] == []


def test_manager_enforces_configured_solver_action_budget(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TGA_MAX_ACTIONS_PER_SOLVER", "1")
    task = _task()
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    snapshot = Manager(store=store, run_root=root, executor=FailedExecutor(ArtifactStore(root / task.id / "artifacts")), solver=MainSolver()).run_session(task.id)
    assert len(snapshot["actions"]) == 1
    assert snapshot["session"]["stop_reason"] == "solver_action_budget_exhausted"


def test_manager_enforces_active_solver_budget_before_selecting_a_solver(tmp_path: Path):
    task = _task()
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    from tga.runtime.session import AgentSession

    AgentSession(store=store, run_root=root, task_id=task.id).ensure(max_turns=4)
    for index in range(3):
        store.add_solver(SolverRecord(id=f"already_active_{index}", task_id=task.id, status="running", model_name="existing"))

    snapshot = Manager(
        store=store,
        run_root=root,
        executor=FailedExecutor(ArtifactStore(root / task.id / "artifacts")),
        solver=MainSolver(),
    ).run_session(task.id)

    assert snapshot["session"]["status"] == "blocked"
    assert snapshot["session"]["stop_reason"] == "active_solver_budget_exhausted"
    assert snapshot["actions"] == []


def test_manager_rejects_invalid_initial_hypothesis_count(tmp_path: Path):
    class InvalidSolver(MainSolver):
        def initial_hypotheses(self, *, task: TGATask, solver_id: str):
            return [HypothesisDraft(f"candidate {index}", "web", task.target, "reason", "test", 0.5) for index in range(6)]

    task = _task()
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    snapshot = Manager(store=store, run_root=root, executor=FailedExecutor(ArtifactStore(root / task.id / "artifacts")), solver=InvalidSolver()).run_session(task.id)
    assert snapshot["session"]["status"] == "failed"
    assert snapshot["session"]["stop_reason"] == "invalid_initial_hypothesis_count"


def test_solver_crash_is_persisted_as_failed_instead_of_leaving_running(tmp_path: Path):
    class CrashingSolver(MainSolver):
        model_name = "invalid-json-solver"

        def initial_hypotheses(self, *, task, solver_id):
            raise ValueError("model response was not valid JSON")

    task = _task()
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)

    snapshot = Manager(
        store=store,
        run_root=root,
        executor=FailedExecutor(ArtifactStore(root / task.id / "artifacts")),
        solver=CrashingSolver(),
    ).run_session(task.id)

    assert snapshot["session"]["status"] == "failed"
    assert snapshot["session"]["stop_reason"] == "solver_initialization_failed"
    assert any(event["type"] == "SOLVER_FAILED" for event in snapshot["agent_events"])
