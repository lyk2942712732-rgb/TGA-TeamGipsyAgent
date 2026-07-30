from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tga.contracts import ActionResult, ActionSpec, ArtifactRecord, SessionInput, TGATask
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import ArtifactImmutableError, PersistenceBundle
from tga.runtime.manager import Manager
from tga.runtime.service import TaskRuntimeService
from tga.domain.task.spec import TaskSpec
from tga.runtime.agents.single_solver_adapter import SingleSolverProvisioner
from tga.runtime.tool_handlers.plan_knowledge import PlanKnowledgeHandler
from tga.runtime.handler_services import ObserverExecutionCoordinator
from tga.runtime.observer import DeterministicObserver, ObserverCoordinator


class _StartManager:
    def start_session(self, *, task_id: str, initial_hint: str | None = None):
        return {"accepted": True, "status": "created"}


def test_initial_prompt_is_persisted_as_authoritative_directive_not_hint(tmp_path) -> None:
    service = TaskRuntimeService(
        run_root=tmp_path / "runs", manager=_StartManager()
    )
    task = TGATask(
        id="task_prompt",
        name="Prompt",
        mode="ctf",
        goal="Solve target",
        session_input=SessionInput(prompt="Inspect the supplied login endpoint."),
    )

    service.create_task(task)

    bundle = PersistenceBundle.open(tmp_path / "runs" / task.id / "evidence.db")
    try:
        spec = bundle.tasks.get_task_spec(task.id)
        assert spec and [item.content for item in spec.instructions] == [
            "Inspect the supplied login endpoint."
        ]
        assert bundle.tasks.list_hints(task.id) == []
        assert bundle.tasks.list_interventions(task.id) == []
    finally:
        bundle.close()


def test_hint_api_adapter_creates_intervention_and_hint_without_memory_strategy_or_knowledge(tmp_path) -> None:
    task = TGATask(id="task_hint", name="Hint", mode="ctf", goal="Solve")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        store.create_task(task)
        bundle = PersistenceBundle(store)
        bundle.tasks.save_task_spec(TaskSpec(task_id=task.id, objective=task.goal))
        manager = Manager(store=store, run_root=tmp_path)

        response = manager.add_hint(task_id=task.id, content="Try the old admin route.")

        assert response["accepted"] is True
        assert response["hint_id"].startswith("hint_")
        assert response["intervention_id"].startswith("intervention_")
        assert bundle.tasks.list_hints(task.id)[0].content == "Try the old admin route."
        assert bundle.tasks.list_interventions(task.id)[0].kind == "hint"
        assert bundle.knowledge.list_knowledge(task.id) == []
        assert store.list_memory(task.id) == []
        assert store.list_strategy_cards(task.id) == []
    finally:
        store.close()


def test_start_session_creates_one_durable_solver_and_new_plan_without_legacy_authority(tmp_path) -> None:
    task = TGATask(id="task_single", name="Single", mode="ctf", goal="Solve")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        store.create_task(task)
        bundle = PersistenceBundle(store)
        bundle.tasks.save_task_spec(TaskSpec(task_id=task.id, objective=task.goal))
        manager = Manager(store=store, run_root=tmp_path)

        assert manager.start_session(task_id=task.id)["accepted"] is True
        assert manager.start_session(task_id=task.id)["accepted"] is True

        solvers = bundle.solvers.list_solvers(task.id)
        plan = bundle.plans.get_global_plan(task.id)
        assert len(solvers) == 1
        assert solvers[0].orchestration_role == "supervisor"
        assert plan and len(plan.intents) == 1
        assert plan.intents[0].assigned_solver_id == solvers[0].id
        assert store.list_strategy_cards(task.id) == []
        assert store.list_memory(task.id) == []
    finally:
        store.close()


def test_manager_lifecycle_facade_keeps_task_orchestrator_state_in_sync(tmp_path) -> None:
    task = TGATask(id="task_facade", name="Facade", mode="ctf", goal="Solve")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        store.create_task(task)
        bundle = PersistenceBundle(store)
        manager = Manager(store=store, run_root=tmp_path)
        assert manager.start_session(task_id=task.id)["accepted"] is True

        assert manager.control_session(task_id=task.id, action="pause")["accepted"] is True
        assert bundle.orchestration.get_state(task.id).status == "paused"
        assert manager.control_session(task_id=task.id, action="resume")["accepted"] is True
        assert bundle.orchestration.get_state(task.id).status == "running"
        assert manager.control_session(task_id=task.id, action="cancel")["accepted"] is True
        assert bundle.orchestration.get_state(task.id).status == "cancelled"
    finally:
        store.close()


def test_legacy_artifact_adapter_is_append_only_and_visible_to_v6_repository(tmp_path) -> None:
    task = TGATask(id="task_artifact_v6", name="Artifact", mode="ctf", goal="Solve")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        store.create_task(task)
        artifact = ArtifactRecord(
            id="artifact_one",
            task_id=task.id,
            kind="tool_output",
            path="artifact_one.txt",
            sha256="a" * 64,
            tool="workspace.read",
            target="input.txt",
            created_at="2026-07-30T00:00:00Z",
        )

        store.add_artifact(artifact)
        store.add_artifact(artifact)

        assert PersistenceBundle(store).evidence.get_artifact(artifact.id) is not None
        with pytest.raises(ArtifactImmutableError):
            store.add_artifact(artifact.model_copy(update={"target": "changed.txt"}))
    finally:
        store.close()


def test_successful_tool_summary_becomes_solver_candidate_knowledge(tmp_path) -> None:
    task = TGATask(id="task_tool_knowledge", name="Knowledge", mode="ctf", goal="Solve")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        store.create_task(task)
        bundle = PersistenceBundle(store)
        bundle.tasks.save_task_spec(TaskSpec(task_id=task.id, objective=task.goal))
        SingleSolverProvisioner(bundle).ensure(task=task, solver_id="solver_main")
        state = SimpleNamespace(task=task, store=store, solver_id="solver_main")

        created = PlanKnowledgeHandler(state).record_action_result(ActionResult(
            action_id="action_one",
            task_id=task.id,
            solver_id="solver_main",
            status="succeeded",
            summary="The endpoint returned HTTP 200.",
        ))

        assert len(created) == 1
        assert created[0].status == "candidate"
        assert created[0].scope == "solver"
        assert created[0].content == "The endpoint returned HTTP 200."
        assert created[0].evidence_claim_ids == []
    finally:
        store.close()


def test_v6_observer_never_writes_legacy_memory_or_strategy_authority(tmp_path) -> None:
    task = TGATask(id="task_observer_v6", name="Observer", mode="ctf", goal="Solve")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    observer = ObserverCoordinator(
        observer=DeterministicObserver(), store=store, cooldown_seconds=0
    )
    try:
        store.create_task(task)
        bundle = PersistenceBundle(store)
        bundle.tasks.save_task_spec(TaskSpec(task_id=task.id, objective=task.goal))
        SingleSolverProvisioner(bundle).ensure(task=task, solver_id="solver_main")
        previous = ActionSpec(
            id="action_previous", task_id=task.id, solver_id="solver_main",
            kind="tool", capability="artifact.inspect", target="fixture",
            rationale="inspect fixture", risk="passive",
        )
        store.add_action(previous, status="failed")
        store.add_action_result(ActionResult(
            action_id=previous.id, task_id=task.id, solver_id="solver_main",
            status="failed", summary="first fixture failure", artifact_ids=["artifact_previous"],
        ))
        current = previous.model_copy(update={"id": "action_current"})
        result = ActionResult(
            action_id=current.id, task_id=task.id, solver_id="solver_main",
            status="failed", summary="second fixture failure", artifact_ids=["artifact_current"],
        )
        state = SimpleNamespace(
            task=task, store=store, solver_id="solver_main", observer=observer,
            observer_directive="",
        )

        ObserverExecutionCoordinator(state).review(action=current, result=result)

        assert store.list_memory(task.id) == []
        assert store.list_strategy_cards(task.id) == []
        assert state.observer_directive
        assert any(
            event.type == "OBSERVER_PATCH_APPLIED"
            and event.payload["legacy_memory_writes"] == 0
            for event in store.list_events(task.id)
        )
    finally:
        observer.close()
        store.close()
