from __future__ import annotations

from pathlib import Path

from tga.contracts import ActionResult, ActionSpec, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.runtime.manager import Manager
from tga.runtime.prompts import build_solver_context
from tga.runtime.solver import MainSolver
from tga.skills.registry import SkillRegistry


def _task() -> TGATask:
    return TGATask(
        id="skill_runtime", name="skill runtime", mode="ctf", target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"], goal="verify skill loading",
    )


class _SuccessExecutor:
    def __init__(self, artifacts: ArtifactStore) -> None:
        self.artifacts = artifacts

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
        artifact = self.artifacts.save_text(
            task_id=task.id, intent_id=None, kind="stdout", text="landing page available", tool="test"
        )
        return ActionResult(
            action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded",
            summary="landing page observed", artifact_ids=[artifact.id], facts=["landing page available"],
        )


def test_skill_registry_selects_bounded_mode_appropriate_turn_context() -> None:
    registry = SkillRegistry()
    skills = registry.for_turn(mode="ctf", attack_class="recon")

    assert 1 <= len(skills) <= 3
    assert skills[0].name == "web-recon"
    context = build_solver_context(task=_task(), snapshot={}, skills=skills)
    assert len(context["skills"]) <= 3
    assert context["skills"][0]["name"] == "web-recon"
    assert "Workflow" in context["skills"][0]["summary"]


def test_manager_records_skill_load_before_each_solver_turn(tmp_path: Path) -> None:
    task = _task()
    run_root = tmp_path / "runs"
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    manager = Manager(
        store=store,
        run_root=run_root,
        executor=_SuccessExecutor(ArtifactStore(run_root / task.id / "artifacts")),
        solver=MainSolver(),
        skills=SkillRegistry(),
    )

    snapshot = manager.run_session(task.id)
    events = [event for event in snapshot["agent_events"] if event["type"] == "SKILLS_LOADED"]

    assert events
    assert events[0]["payload"]["hypothesis_id"]
    assert events[0]["payload"]["skills"][0]["name"] == "web-recon"
