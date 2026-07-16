from pathlib import Path

import pytest

from tga.contracts import ActionResult, ActionSpec, SolverRecord, SubagentOutput, SubagentRequest, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore, utc_now
from tga.runtime.board import BoardStore, HypothesisDraft
from tga.runtime.challenge_state import ChallengeStateMachine
from tga.runtime.manager import Manager
from tga.runtime.session import AgentSession
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


def test_default_manager_starts_three_child_roles_and_stops_all_on_confirmed_flag(tmp_path: Path) -> None:
    task = _task()
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)

    snapshot = Manager(
        store=store,
        run_root=root,
        executor=FlagExecutor(ArtifactStore(root / task.id / "artifacts")),
    ).run_session(task.id)

    assert snapshot["challenge"]["status"] == "solved"
    assert snapshot["challenge"]["completion_proof_artifact_id"] == snapshot["flags"][0]["evidence_artifact_id"]
    assert {item["role"] for item in snapshot["solvers"]} == {"main", "recon", "targeted", "research"}
    assert all(item["status"] == "completed" for item in snapshot["solvers"])
    assert {item["status"] for item in snapshot["subagents"]} == {"completed"}
    types = [item["type"] for item in snapshot["agent_events"]]
    assert types.count("SUBAGENT_STARTED") == 3
    assert types.count("SUBAGENT_FINISHED") == 3
    assert types.index("FLAG_CONFIRMED") < types.index("SESSION_STOPPED")
    assert (root / task.id / "reports" / "report.md").is_file()


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
