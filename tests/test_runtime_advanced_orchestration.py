from pathlib import Path

import pytest

from tga.contracts import ActionResult, ActionSpec, SolverRecord, SubagentOutput, SubagentRequest, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore, utc_now
from tga.runtime.board import BoardStore, HypothesisDraft
from tga.runtime.challenge_state import ChallengeStateMachine
from tga.runtime.manager import Manager
from tga.runtime.session import AgentSession
from tga.runtime.solver import MainSolver
from tga.runtime.solver_pool import SolverPool


def _task(task_id: str = "advanced_runtime") -> TGATask:
    return TGATask(
        id=task_id,
        name="advanced runtime",
        mode="ctf",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        goal="solve",
        flag_format=r"flag\{[^}]+\}",
    )


class FlagExecutor:
    def __init__(self, artifact_store: ArtifactStore):
        self.artifact_store = artifact_store

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
        artifact = self.artifact_store.save_text(
            task_id=task.id,
            intent_id=None,
            kind="stdout",
            text="flag{advanced_orchestration}",
            tool="advanced-test",
            target=task.target,
        )
        return ActionResult(
            action_id=action.id,
            task_id=task.id,
            solver_id=action.solver_id,
            status="succeeded",
            summary="artifact contains the challenge flag",
            artifact_ids=[artifact.id],
            candidate_flags=["flag{advanced_orchestration}"],
        )


def test_experimental_subagents_start_three_child_roles_and_stop_all_on_confirmed_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TGA_ENABLE_EXPERIMENTAL_SUBAGENTS", "1")
    monkeypatch.setattr("tga.runtime.manager.build_runtime_solver", MainSolver)
    task = _task()
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)

    manager = Manager(
        store=store,
        run_root=root,
        executor=FlagExecutor(ArtifactStore(root / task.id / "artifacts")),
    )
    snapshot = manager.run_session(task.id)

    assert snapshot["challenge"]["status"] == "solved"
    assert snapshot["challenge"]["completion_proof_artifact_id"] == snapshot["flags"][0]["evidence_artifact_id"]
    assert {item["role"] for item in snapshot["solvers"]} == {"main", "recon", "targeted", "research"}
    # Compatibility planners are closed after the run and never become the
    # persistent product AgentSession registry.
    assert manager._solver_instances == {}
    assert all(item["status"] == "completed" for item in snapshot["solvers"])
    assert {item["status"] for item in snapshot["subagents"]} == {"completed"}
    types = [item["type"] for item in snapshot["agent_events"]]
    assert types.count("SUBAGENT_STARTED") == 3
    assert types.count("SUBAGENT_FINISHED") == 3
    assert types.index("FLAG_CONFIRMED") < types.index("SESSION_STOPPED")
    assert (root / task.id / "reports" / "report.md").is_file()
    for solver in snapshot["solvers"]:
        state_path = root / task.id / "solvers" / solver["id"] / "session" / "state.json"
        assert state_path.is_file()
        assert f'"solver"' in state_path.read_text(encoding="utf-8")


def test_challenge_solved_transition_requires_task_owned_artifact(tmp_path: Path) -> None:
    task = _task("challenge_state")
    store = EvidenceStore(tmp_path / "evidence.db")
    store.create_task(task)
    machine = ChallengeStateMachine(store)
    machine.activate(task)

    with pytest.raises(ValueError, match="completion proof"):
        machine.transition(task.id, "solved", reason="invalid", proof_artifact_id="missing")


def test_solver_pool_rejects_duplicate_request_and_uses_private_workspaces(tmp_path: Path) -> None:
    task = _task("solver_pool")
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    AgentSession(store=store, run_root=root, task_id=task.id).ensure(max_turns=8)
    store.update_session(task.id, status="running")
    main = SolverRecord(id="solver_main", task_id=task.id, status="running", started_at=utc_now())
    store.add_solver(main)
    hypothesis = BoardStore(store).create_hypothesis(
        task_id=task.id,
        owner_solver_id=main.id,
        draft=HypothesisDraft("landing page is reachable", "recon", task.target, "no inventory", "fetch root", 0.8),
    )
    request = SubagentRequest(
        id="subreq_recon",
        task_id=task.id,
        parent_solver_id=main.id,
        role="recon",
        objective="map the authorized landing surface",
        hypothesis_ids=[hypothesis.id],
        max_actions=2,
    )
    pool = SolverPool(store=store, run_root=root)
    child = pool.start(request)

    assert pool.workspace(child.id, task.id).is_dir()
    assert (pool.workspace(child.id, task.id) / "request.json").is_file()
    assert pool.workspace(child.id, task.id) != pool.workspace(main.id, task.id)
    # Legacy inspection follows the Session allowance, not the child request.
    assert Manager(store=store, run_root=root)._solver_action_limit(store, task.id, child.id) == 8
    with pytest.raises(ValueError, match="equivalent subagent"):
        pool.start(request.model_copy(update={"id": "subreq_duplicate"}))


def test_subagent_output_cannot_reference_another_solver_artifact(tmp_path: Path) -> None:
    task = _task("subagent_ownership")
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    AgentSession(store=store, run_root=root, task_id=task.id).ensure(max_turns=8)
    store.update_session(task.id, status="running")
    main = SolverRecord(id="solver_main", task_id=task.id, status="running", started_at=utc_now())
    store.add_solver(main)
    request = SubagentRequest(
        id="subreq_targeted",
        task_id=task.id,
        parent_solver_id=main.id,
        role="targeted",
        objective="validate one route",
        max_actions=2,
    )
    child = SolverPool(store=store, run_root=root).start(request)
    artifact = ArtifactStore(root / task.id / "artifacts").save_text(
        task_id=task.id,
        intent_id=None,
        kind="stdout",
        text="foreign evidence",
        tool="test",
        target=task.target,
    )
    store.add_artifact(artifact)
    output = SubagentOutput(
        request_id=request.id,
        solver_id=child.id,
        status="completed",
        artifact_ids=[artifact.id],
    )

    with pytest.raises(ValueError, match="unowned artifacts"):
        Manager(store=store, run_root=root).accept_subagent_output(task_id=task.id, output=output)


def test_legacy_child_action_budget_is_not_an_execution_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TGA_MAX_ACTIONS_PER_SOLVER", "1")
    task = _task("solver_handoff")
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    AgentSession(store=store, run_root=root, task_id=task.id).ensure(max_turns=8)
    store.update_session(task.id, status="running")
    main = SolverRecord(id="solver_main", task_id=task.id, status="running", started_at=utc_now())
    store.add_solver(main)
    hypothesis = BoardStore(store).create_hypothesis(
        task_id=task.id,
        owner_solver_id=main.id,
        draft=HypothesisDraft("input may execute", "web", task.target, "form observed", "test input", 0.8),
    )
    child = SolverPool(store=store, run_root=root).start(SubagentRequest(
        id="subreq_targeted",
        task_id=task.id,
        parent_solver_id=main.id,
        role="targeted",
        objective="test one input",
        hypothesis_ids=[hypothesis.id],
        max_actions=1,
    ))
    store.add_action(ActionSpec(
        id="action_child_budget",
        task_id=task.id,
        solver_id=child.id,
        hypothesis_id=hypothesis.id,
        kind="http",
        capability="http.request",
        target=task.target,
        arguments={"method": "GET"},
        rationale="consume the child allowance",
        risk="passive",
    ))

    selected_solver, selected_hypothesis = Manager(store=store, run_root=root)._next_role_assignment(store, task.id)
    assert selected_solver.id == child.id
    assert selected_hypothesis.id == hypothesis.id
