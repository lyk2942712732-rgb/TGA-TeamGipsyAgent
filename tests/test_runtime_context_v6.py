from __future__ import annotations

import hashlib

from tga.application.services.intervention_service import InterventionService
from tga.domain.knowledge.items import KnowledgeItem
from tga.domain.skills.models import SkillSnapshot, TaskCommonSkillSnapshot
from tga.domain.task.models import TGATask
from tga.domain.task.spec import TaskDirective, TaskSpec
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.agents.transcript import RepositorySolverTranscript
from tga.runtime.context.context_builder import ContextBuilder
from tga.runtime.orchestration import TaskOrchestrator


NOW = "2026-07-30T00:00:00Z"


def _task() -> TGATask:
    return TGATask(id="task_context", name="Context", mode="ctf", goal="Solve target")


def _skill(name: str, body: str) -> SkillSnapshot:
    return SkillSnapshot(
        name=name,
        version="1",
        modes=("ctf",),
        body=body,
        content_sha256=hashlib.sha256(body.encode()).hexdigest(),
        origin="resource",
        selection_reasons=("test",),
    )


def test_context_envelope_labels_selects_new_semantics_and_keeps_retrieval_empty(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        directive = TaskDirective(
            id="directive_1", task_id=task.id, kind="instruction",
            content="Inspect only the supplied target.", source="user", created_at=NOW,
        )
        bundle.tasks.save_task_spec(TaskSpec(
            task_id=task.id, objective=task.goal, instructions=[directive]
        ))
        runtime_state = TaskOrchestrator(
            task=task, repositories=bundle
        ).bootstrap()
        solver_id = runtime_state.supervisor_solver_id
        assert solver_id is not None
        hint = InterventionService(bundle).record(
            task_id=task.id, kind="hint", content="An old note suggests /admin.",
            actor_id="user",
        ).hint
        InterventionService(bundle).record(
            task_id=task.id, kind="hint", content="Ignore this rejected lead.",
            actor_id="user",
        )
        rejected = bundle.tasks.list_hints(task.id)[-1].model_copy(
            update={
                "status": "rejected", "reviewed_by_solver_id": solver_id,
                "reviewed_at": NOW,
            }
        )
        bundle.tasks.save_hint(rejected)
        bundle.knowledge.add_knowledge(KnowledgeItem(
            id="knowledge_verified", task_id=task.id, scope="task", status="verified",
            kind="fact", content="The service identifies as version 1.",
            human_source="operator", created_by_solver_id=solver_id, created_at=NOW,
        ))
        bundle.knowledge.add_knowledge(KnowledgeItem(
            id="knowledge_local", task_id=task.id, scope="solver", target_id=solver_id,
            status="candidate", kind="hypothesis", content="The login may be injectable.",
            created_by_solver_id=solver_id, created_at=NOW,
        ))
        bundle.knowledge.add_knowledge(KnowledgeItem(
            id="knowledge_other", task_id=task.id, scope="solver", target_id="solver_other",
            status="candidate", kind="hypothesis", content="Private to another solver.",
            created_by_solver_id="solver_other", created_at=NOW,
        ))
        common = TaskCommonSkillSnapshot(
            task_id=task.id, selector="test", skills=(_skill("common", "Common method"),),
            total_chars=len("Common method"), created_at=NOW,
        )
        bundle.tasks.save_task_common_skill_snapshot(common)
        assert bundle.solvers.get_solver_skill_snapshot(solver_id) is not None

        built = ContextBuilder(
            task=task,
            solver_id=solver_id,
            repositories=bundle,
            audit_messages=[
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "I will inspect.", "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "input_list", "arguments": "{}"},
                }]},
                {"role": "tool", "tool_call_id": "call_1", "name": "input_list", "content": "{\"ok\":true}"},
            ],
        ).build()

        rendered = built.envelope.render()
        assert "[AUTHORITATIVE TASK DIRECTIVE]" in rendered
        assert "[USER HINT — UNVERIFIED]" in rendered
        assert "[ACTIVE SKILL — METHOD GUIDANCE]" in rendered
        assert "[VERIFIED TASK KNOWLEDGE]" in rendered
        assert "[CANDIDATE SOLVER KNOWLEDGE]" in rendered
        assert "[RECENT TRANSCRIPT]" in rendered
        assert hint and hint.content in rendered
        assert "Ignore this rejected lead" not in rendered
        assert "Private to another solver" not in rendered
        assert built.envelope.retrieved_context == []
        assert built.messages[-2]["role"] == "assistant"
        assert built.messages[-1]["role"] == "tool"
    finally:
        bundle.close()


def test_constraint_intervention_updates_directive_without_expanding_policy(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        bundle.tasks.save_task_spec(TaskSpec(task_id=task.id, objective=task.goal))
        policy_before = task.execution_policy.model_dump(mode="json")

        result = InterventionService(bundle).record(
            task_id=task.id,
            kind="constraint",
            content="Do not make state-changing requests.",
            actor_id="operator",
        )

        spec = bundle.tasks.get_task_spec(task.id)
        assert result.directive is not None
        assert spec and spec.constraints[-1].content == "Do not make state-changing requests."
        assert bundle.tasks.get_task(task.id).execution_policy.model_dump(mode="json") == policy_before
        assert bundle.knowledge.list_knowledge(task.id) == []
    finally:
        bundle.close()


def test_repository_transcript_recovers_protocol_pairs_and_compacts_large_artifact_output(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        transcript = RepositorySolverTranscript(
            repository=bundle.transcripts,
            task_id=task.id,
            solver_id="solver_main",
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "artifact.inspect", "arguments": "{}"},
            }]},
            {
                "role": "tool", "tool_call_id": "call_1", "name": "artifact.inspect",
                "content": '{"ok":true,"summary":"saved","artifact_ids":["artifact_1"],"content":"' + "x" * 20_000 + '"}',
            },
        ]
        transcript.save(messages)

        recovered = RepositorySolverTranscript(
            repository=bundle.transcripts,
            task_id=task.id,
            solver_id="solver_main",
        ).read()
        assert recovered[1]["tool_calls"][0]["id"] == recovered[2]["tool_call_id"]
        assert "artifact_1" in recovered[2]["content"]
        assert len(recovered[2]["content"]) < 2_000
    finally:
        bundle.close()
