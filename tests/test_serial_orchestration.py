from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tga.contracts import ResourceProvenance, SessionFile, SessionInput, TGATask
from tests.runtime_fixtures import task as v6_task
from tga.domain.evidence.artifacts import Artifact
from tga.domain.evidence.claims import EvidenceClaim
from tga.domain.evidence.findings import Finding
from tga.domain.evidence.locators import EvidenceLocator
from tga.domain.knowledge.items import KnowledgeItem
from tga.domain.task.spec import TaskSpec
from tga.domain.solver import (
    ReportResult,
    ReviewResult,
    SolverInstance,
    WorkerCoverage,
    WorkerResult,
)
from tga.infrastructure.persistence import PersistenceBundle, PersistenceConflict
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.runtime.orchestration import TaskOrchestrator
from tests.capability_fixtures import empty_mcp_catalog
from tga.runtime.tooling.catalog.manifest_builder import ToolManifestBuilder
from tga.runtime.tooling.requests import ActionContext, ModelToolIntent, ToolRequest
from tga.runtime.tooling.results import RawExecutionResult
from tga.runtime.tooling.routing import ToolGovernanceGateway
from tga.runtime.context import SessionContextBuilder
from tga.evidence.database import utc_now


MODES_AND_WORKERS = {
    "ctf": "challenge-classifier",
    "penetration_test": "surface-mapper",
    "incident_response": "evidence-triage-solver",
    "vulnerability_research": "architecture-analyst",
    "reverse_engineering": "binary-triage-solver",
}


def _task(mode: str = "ctf", *, task_id: str | None = None) -> TGATask:
    return v6_task(
        id=task_id or f"serial_{mode}",
        name=f"serial {mode}",
        mode=mode,
        goal="complete the task through a bounded solver team",
        execution_budget={"max_total_solvers": 8, "max_tool_calls": 100},
    )


def _orchestrator(tmp_path: Path, task: TGATask):
    bundle = PersistenceBundle.open(tmp_path / task.id / "evidence.db")
    bundle.tasks.create_task(task)
    # TaskSpec is the authoritative resource source, exactly as in
    # TaskService.create_task; the orchestrator authorizes against it.
    bundle.tasks.save_task_spec(TaskSpec(
        task_id=task.id,
        objective=task.goal,
        resources=[item.resource_ref() for item in task.session_input.files],
        provenance={"source": "test_fixture", "session_resources_projected": True},
    ))
    return bundle, TaskOrchestrator(task=task, repositories=bundle)


@pytest.mark.parametrize("mode,expected_worker", MODES_AND_WORKERS.items())
def test_five_modes_bootstrap_supervisor_and_dispatch_one_worker(
    tmp_path: Path, mode: str, expected_worker: str
) -> None:
    task = _task(mode)
    task = task.model_copy(update={
        "execution_policy": task.execution_policy.model_copy(update={
            "local_compute": task.execution_policy.local_compute.model_copy(
                update={"mode": "isolated"}
            )
        })
    })
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        assignment = orchestrator.dispatch_next()

        assert state.status == "running"
        assert state.supervisor_solver_id
        assert assignment is not None
        worker = bundle.solvers.get_solver(assignment.solver_id)
        supervisor = bundle.solvers.get_solver(state.supervisor_solver_id)
        assert worker is not None and worker.definition_id == expected_worker
        definition = orchestrator.definitions.require(expected_worker)
        assert worker.capability_binding_snapshot.host_capability_profile_id == (
            definition.host_capability_profile_id
        )
        assert worker.definition_snapshot == definition
        assert worker.execution_policy_snapshot == task.execution_policy
        assert worker.capability_binding_snapshot.host_capabilities
        assert tuple(
            item.id for item in worker.capability_binding_snapshot.host_capabilities
        ) == worker.capability_binding_snapshot.host_capability_ids
        if definition.kali is not None:
            assert worker.capability_binding_snapshot.kali_runtime is not None
            assert worker.capability_binding_snapshot.kali_profile is not None
            assert worker.capability_binding_snapshot.sandbox_config_digest
        assert SolverInstance.model_validate_json(
            worker.model_dump_json()
        ) == worker
        assert worker.parent_solver_id == supervisor.id
        assert assignment.intent.id == assignment.intent_id
        assert assignment.capability_binding_snapshot == worker.capability_binding_snapshot
        assert assignment.budget == worker.budget
        assert worker.transcript_ref != supervisor.transcript_ref
        assert worker.private_workspace_ref != supervisor.private_workspace_ref
        assert len([
            item for item in bundle.solvers.list_solvers(task.id)
            if item.orchestration_role == "worker"
            and str(item.status) in {"queued", "running", "awaiting_approval"}
        ]) == 1
        assert orchestrator.dispatch_next() is None
    finally:
        bundle.close()


def test_dispatch_blocks_unsupported_intent_without_poisoning_valid_work(
    tmp_path: Path,
) -> None:
    task = _task(task_id="serial_unsupported_intent")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        unsupported = orchestrator.create_intent(
            supervisor_solver_id=state.supervisor_solver_id,
            kind="web_exploitation",
            title="Model-authored unsupported alias",
            objective="Do not let this legacy node block valid work",
            priority=10,
        )

        assignment = orchestrator.dispatch_next()

        assert assignment is not None
        assert assignment.intent.kind == "challenge_classification"
        plan = bundle.plans.get_global_plan(task.id)
        blocked = next(item for item in plan.intents if item.id == unsupported.id)
        assert blocked.status == "blocked"
        event = next(
            item for item in bundle.events.list_agent_events(task.id)
            if item.type == "INTENT_BLOCKED" and item.intent_id == unsupported.id
        )
        assert event.payload["reason"] == "unsupported_intent_kind"
        assert event.payload["error"]["code"] == "INTENT_KIND_UNSUPPORTED"
    finally:
        bundle.close()


def test_supervisor_gateway_rejects_unassignable_intent_kind(tmp_path: Path) -> None:
    task = _task(task_id="serial_gateway_intent_kind")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        before = bundle.plans.get_global_plan(task.id)
        handler = orchestrator.gateway_control_handlers(
            state.supervisor_solver_id
        )["create_intent"]

        result = handler({
            "kind": "command_injection",
            "title": "Unsupported alias",
            "objective": "Must be rejected before persistence",
        })

        assert result["ok"] is False
        assert result["status"] == "rejected"
        assert result["error"]["code"] == "INTENT_KIND_UNSUPPORTED"
        assert "ctf_web" in result["supported_intent_kinds"]
        assert bundle.plans.get_global_plan(task.id).version == before.version
        assert len(bundle.plans.get_global_plan(task.id).intents) == len(before.intents)
    finally:
        bundle.close()


def test_supervisor_worker_result_and_completion_are_separate_and_idempotent(tmp_path: Path) -> None:
    task = _task(task_id="serial_complete")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        assignment = orchestrator.dispatch_next()
        assert assignment is not None
        result = WorkerResult(
            task_id=task.id,
            solver_id=assignment.solver_id,
            intent_id=assignment.intent_id,
            status="succeeded",
            summary="recon completed",
            coverage=WorkerCoverage(completed=("recon",)),
        )

        result_id = orchestrator.submit_worker_result(result)
        assert orchestrator.submit_worker_result(result) == result_id
        assert bundle.plans.get_global_plan(task.id).intents[0].status == "completed"
        assert bundle.solvers.get_solver(assignment.solver_id).status == "completed"
        assert orchestrator.state().status == "running"

        with pytest.raises(PermissionError, match="Supervisor"):
            orchestrator.complete_task(
                solver_id=assignment.solver_id,
                proposal={"summary": "worker cannot complete"},
                validator=lambda _proposal: {"accepted": True},
            )

        completion = orchestrator.complete_task(
            solver_id=state.supervisor_solver_id,
            proposal={"summary": "supervisor completed after worker evidence"},
            validator=lambda proposal: {"accepted": True, "summary": proposal["summary"]},
        )
        assert completion["accepted"] is True
        assert orchestrator.state().status == "completed"
        assert len(bundle.solvers.list_worker_results(task.id)) == 1
    finally:
        bundle.close()


def test_blocked_worker_does_not_block_task_and_alternative_intent_runs(tmp_path: Path) -> None:
    task = _task(task_id="serial_alternative")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        orchestrator.create_intent(
            supervisor_solver_id=state.supervisor_solver_id,
            kind="validation",
            title="Alternative validation",
            objective="validate through an independent route",
            priority=-1,
        )
        first = orchestrator.dispatch_next()
        assert first is not None
        orchestrator.submit_worker_result(WorkerResult(
            task_id=task.id,
            solver_id=first.solver_id,
            intent_id=first.intent_id,
            status="blocked",
            summary="primary route unavailable",
            limitations=("target unavailable",),
        ))

        assert orchestrator.state().status == "running"
        second = orchestrator.dispatch_next()
        assert second is not None
        assert second.intent_id != first.intent_id
    finally:
        bundle.close()


def test_reviewer_reporter_pipeline_and_authority_boundaries(tmp_path: Path) -> None:
    task = _task(task_id="serial_review_report")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        assignment = orchestrator.dispatch_next()
        assert assignment is not None
        now = utc_now()
        artifact = Artifact(
            id="artifact_candidate",
            task_id=task.id,
            intent_id=assignment.intent_id,
            kind="tool_output",
            path="candidate.txt",
            sha256="a" * 64,
            created_at=now,
        )
        claim = EvidenceClaim(
            id="claim_candidate",
            task_id=task.id,
            statement="candidate statement",
            artifact_id=artifact.id,
            locator=EvidenceLocator(kind="text_range", char_start=0, char_end=9),
            created_by_solver_id=assignment.solver_id,
            created_at=now,
        )
        knowledge = KnowledgeItem(
            id="knowledge_candidate",
            task_id=task.id,
            scope="solver",
            target_id=assignment.solver_id,
            status="candidate",
            kind="fact",
            content="candidate statement",
            evidence_claim_ids=[claim.id],
            created_by_solver_id=assignment.solver_id,
            created_at=now,
        )
        finding = Finding(
            id="finding_candidate",
            task_id=task.id,
            title="Candidate finding",
            severity="medium",
            evidence_claims=[claim],
            created_by_solver_id=assignment.solver_id,
            created_at=now,
        )
        bundle.evidence.add_artifact(artifact)
        bundle.evidence.add_evidence_claim(claim)
        bundle.knowledge.add_knowledge(knowledge)
        bundle.evidence.save_finding(finding)
        worker_result_id = orchestrator.submit_worker_result(WorkerResult(
            task_id=task.id,
            solver_id=assignment.solver_id,
            intent_id=assignment.intent_id,
            status="succeeded",
            summary="candidate evidence produced",
            candidate_evidence_claim_ids=("claim_candidate",),
            candidate_knowledge_ids=("knowledge_candidate",),
            finding_ids=("finding_candidate",),
        ))

        reviewer = orchestrator.request_review(
            supervisor_solver_id=state.supervisor_solver_id
        )
        review_id = orchestrator.record_review(ReviewResult(
            task_id=task.id,
            solver_id=reviewer.id,
            worker_result_ids=(worker_result_id,),
            status="confirmed",
            confirmed_evidence_claim_ids=("claim_candidate",),
            confirmed_knowledge_ids=("knowledge_candidate",),
            confirmed_finding_ids=("finding_candidate",),
        ))
        reporter = orchestrator.request_report(
            supervisor_solver_id=state.supervisor_solver_id
        )
        report_id = orchestrator.record_report(ReportResult(
            task_id=task.id,
            solver_id=reporter.id,
            review_result_ids=(review_id,),
            status="completed",
            summary="confirmed report generated",
            report_artifact_id="artifact_report",
        ))

        assert report_id.startswith("report_result_")
        assert bundle.evidence.get_evidence_claim(claim.id).status == "confirmed"
        assert bundle.knowledge.get_knowledge(knowledge.id).status == "verified"
        assert bundle.evidence.get_finding(finding.id).status == "confirmed"
        assert bundle.solvers.get_solver(reviewer.id).status == "completed"
        assert bundle.solvers.get_solver(reporter.id).status == "completed"
        with pytest.raises(PermissionError, match="Reporter"):
            orchestrator.confirm_finding(
                solver_id=reporter.id, finding_id="finding_candidate"
            )
    finally:
        bundle.close()


def test_restart_merges_persisted_worker_result_without_duplicate_solver(tmp_path: Path) -> None:
    task = _task(task_id="serial_recovery")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        orchestrator.bootstrap()
        assignment = orchestrator.dispatch_next()
        assert assignment is not None
        result = WorkerResult(
            task_id=task.id,
            solver_id=assignment.solver_id,
            intent_id=assignment.intent_id,
            status="succeeded",
            summary="persisted before orchestrator crash",
        )
        result_id = bundle.solvers.save_worker_result(result)
        before = tuple(item.id for item in bundle.solvers.list_solvers(task.id))

        restarted = TaskOrchestrator(task=task, repositories=bundle)
        recovered = restarted.recover()
        restarted.recover()

        assert result_id in recovered.merged_worker_result_ids
        assert tuple(item.id for item in bundle.solvers.list_solvers(task.id)) == before
        assert bundle.plans.get_global_plan(task.id).intents[0].status == "completed"
        assert restarted.dispatch_next() is None
        assert [
            item.type for item in bundle.events.list_agent_events(task.id)
        ].count("WORKER_RESULT_MERGED") == 1
    finally:
        bundle.close()


def test_worker_skill_tool_policy_and_resource_assignment_are_isolated(tmp_path: Path) -> None:
    task = _task(task_id="serial_isolation")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        intent = orchestrator.create_intent(
            supervisor_solver_id=state.supervisor_solver_id,
            kind="validation",
            title="Scoped validation",
            objective="inspect only the assigned resource",
            allowed_resource_ids=("artifact_allowed",),
            relevant_knowledge_ids=("knowledge_allowed",),
            relevant_evidence_claim_ids=("claim_allowed",),
            priority=10,
        )
        assignment = orchestrator.dispatch_next()
        assert assignment is not None and assignment.intent_id == intent.id
        assert assignment.allowed_resources == ("artifact_allowed",)
        assert assignment.relevant_knowledge_ids == ("knowledge_allowed",)
        assert assignment.relevant_evidence_claim_ids == ("claim_allowed",)
        assert assignment.skill_snapshot is not None
        assert assignment.skill_snapshot.solver_id == assignment.solver_id
        assert assignment.skill_snapshot.intent_id == assignment.intent_id
        assert "propose_task_completion" not in assignment.allowed_control_tools
        assert "submit_worker_result" in assignment.allowed_control_tools
    finally:
        bundle.close()


def test_orchestration_limits_prevent_recursive_solver_creation(tmp_path: Path) -> None:
    task = _task(task_id="serial_limits").model_copy(update={
        "execution_budget": {"max_total_solvers": 2, "max_tool_calls": 100}
    })
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        assignment = orchestrator.dispatch_next()
        assert assignment is not None
        with pytest.raises(PersistenceConflict, match="max_total_solvers"):
            orchestrator.request_review(
                supervisor_solver_id=state.supervisor_solver_id
            )
    finally:
        bundle.close()


def test_pause_resume_replaces_worker_run_generation(tmp_path: Path) -> None:
    task = _task(task_id="serial_pause_resume")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        orchestrator.bootstrap()
        assignment = orchestrator.dispatch_next()
        assert assignment is not None

        paused = orchestrator.pause(reason="user_paused")
        assert paused.status == "paused"
        assert bundle.solvers.get_solver(assignment.solver_id).status == "paused"

        resumed = orchestrator.resume()
        assert resumed.status == "running"
        assert bundle.solvers.get_solver(assignment.solver_id).status == "paused"
        assert orchestrator.dispatch_next() is None
        assert bundle.orchestration.get_assignment(assignment.id).status == "cancelled"
        replacements = [
            item for item in bundle.orchestration.list_assignments(task.id)
            if item.intent_id == assignment.intent_id and item.attempt == 2
        ]
        assert len(replacements) == 1
        assert replacements[0].solver_id != assignment.solver_id
        assert bundle.solvers.get_solver(replacements[0].solver_id).status == "queued"
    finally:
        bundle.close()


def test_cancel_is_terminal_and_cancels_nonterminal_solvers_and_intents(tmp_path: Path) -> None:
    task = _task(task_id="serial_cancel")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        orchestrator.bootstrap()
        assignment = orchestrator.dispatch_next()
        assert assignment is not None

        cancelled = orchestrator.cancel(reason="user_cancelled")

        assert cancelled.status == "cancelled"
        assert bundle.solvers.get_solver(assignment.solver_id).status == "cancelled"
        assert {
            item.status for item in bundle.plans.get_global_plan(task.id).intents
        } == {"cancelled"}
        assert orchestrator.cancel(reason="duplicate_cancel").status == "cancelled"
        with pytest.raises(PersistenceConflict, match="cannot resume"):
            orchestrator.resume()
    finally:
        bundle.close()


def test_failed_reviewer_and_reporter_retry_with_new_attempt(tmp_path: Path) -> None:
    task = _task(task_id="serial_role_retry")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        reviewer_one = orchestrator.request_review(
            supervisor_solver_id=state.supervisor_solver_id
        )
        bundle.solvers.update_solver_status(reviewer_one.id, "failed")
        reviewer_two = orchestrator.request_review(
            supervisor_solver_id=state.supervisor_solver_id
        )
        reporter_one = orchestrator.request_report(
            supervisor_solver_id=state.supervisor_solver_id
        )
        bundle.solvers.update_solver_status(reporter_one.id, "failed")
        reporter_two = orchestrator.request_report(
            supervisor_solver_id=state.supervisor_solver_id
        )

        assert reviewer_two.id != reviewer_one.id
        assert reviewer_two.id.endswith("_a2")
        assert reporter_two.id != reporter_one.id
        assert reporter_two.id.endswith("_a2")
    finally:
        bundle.close()


def test_worker_submit_result_routes_through_gateway_without_legacy_execution(tmp_path: Path) -> None:
    task = _task(task_id="serial_gateway")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        orchestrator.bootstrap()
        assignment = orchestrator.dispatch_next()
        assert assignment is not None
        solver = bundle.solvers.get_solver(assignment.solver_id)
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        catalog = empty_mcp_catalog()
        manifest = ToolManifestBuilder().build(
            task=task,
            solver=solver,
            definition=definition,
            intent=assignment.intent,
            catalog=catalog,
        )

        class Adapter:
            calls = 0

            def execute(self, action):
                self.calls += 1
                return RawExecutionResult(
                    action_id=action.id,
                    status="succeeded",
                    output={"ok": True},
                )

        adapter = Adapter()
        context = ActionContext(
            task_id=task.id,
            solver_id=solver.id,
            intent_id=assignment.intent_id,
            local_plan_step_id=f"local_step_{solver.id}_1",
            orchestration_role="worker",
            solver_definition_id=solver.definition_id,
            execution_policy_snapshot_id="execution:" + "a" * 64,
            solver_capability_snapshot_id="tool:" + "b" * 64,
            attempt=1,
            created_at="2026-07-30T00:00:00Z",
        )
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            execution_adapter=adapter,
            control_handlers=orchestrator.gateway_control_handlers(solver.id),
            allowed_resource_ids=assignment.allowed_resources,
        )
        response = gateway.handle(ToolRequest(
            provider_tool_name="submit_worker_result",
            arguments={"status": "succeeded", "summary": "worker finished"},
            model_intent=ModelToolIntent(rationale="return structured result"),
            action_context=context,
            tool_call_id="call_submit_worker_result",
        ))
        forbidden = gateway.handle(ToolRequest(
            provider_tool_name="propose_task_completion",
            arguments={"summary": "forged completion"},
            model_intent=ModelToolIntent(rationale="complete task"),
            action_context=context,
            tool_call_id="call_worker_completion",
        ))

        assert response.status == "succeeded"
        assert response.model_payload["terminal"] is True
        assert adapter.calls == 0
        assert forbidden.error and forbidden.error.code == "TOOL_NOT_IN_MANIFEST"
        assert len(bundle.solvers.list_worker_results(task.id)) == 1
    finally:
        bundle.close()


def test_serial_recon_validator_reviewer_reporter_pipeline(tmp_path: Path) -> None:
    task = _task(task_id="serial_full_pipeline")
    bundle, orchestrator = _orchestrator(tmp_path, task)
    definitions_seen: list[str] = []
    try:
        state = orchestrator.bootstrap()
        initial = bundle.plans.get_global_plan(task.id).intents[0]
        orchestrator.create_intent(
            supervisor_solver_id=state.supervisor_solver_id,
            kind="validation",
            title="Validate recon result",
            objective="independently validate the candidate",
            dependencies=(initial.id,),
        )

        def worker_runtime(assignment):
            solver = bundle.solvers.get_solver(assignment.solver_id)
            definitions_seen.append(solver.definition_id)
            return WorkerResult(
                task_id=task.id,
                solver_id=assignment.solver_id,
                intent_id=assignment.intent_id,
                status="succeeded",
                summary=f"{solver.definition_id} completed",
            )

        def reviewer_runtime(solver, worker_result_ids):
            return ReviewResult(
                task_id=task.id,
                solver_id=solver.id,
                worker_result_ids=worker_result_ids,
                status="confirmed",
            )

        def reporter_runtime(solver, review_result_ids):
            return ReportResult(
                task_id=task.id,
                solver_id=solver.id,
                review_result_ids=review_result_ids,
                status="completed",
                summary="evidence-backed serial report",
            )

        final = orchestrator.run_serial(
            worker_runtime=worker_runtime,
            reviewer_runtime=reviewer_runtime,
            reporter_runtime=reporter_runtime,
            completion_proposal={"summary": "serial team completed"},
            completion_validator=lambda _proposal: {"accepted": True},
        )

        assert definitions_seen == ["challenge-classifier", "flag-verifier"]
        assert final.status == "completed"
        roles = [item.orchestration_role for item in bundle.solvers.list_solvers(task.id)]
        assert roles.count("worker") == 2
        assert roles.count("reviewer") == 1
        assert roles.count("reporter") == 1
        events = [item.type for item in bundle.events.list_agent_events(task.id)]
        assert events.count("WORKER_RESULT_MERGED") == 2
        assert "REVIEW_RESULT_RECORDED" in events
        assert "REPORT_RESULT_RECORDED" in events
        assert "TASK_COMPLETION_PROPOSED" in events
    finally:
        bundle.close()


def test_worker_context_and_gateway_hide_unassigned_task_inputs(tmp_path: Path) -> None:
    first = SessionFile(
        id="asset_" + "a" * 32,
        originalName="allowed.txt",
        storedName="a" * 32 + ".txt",
        relativePath="inputs/files/" + "a" * 32 + ".txt",
        mimeType="text/plain",
        mediaKind="text",
        size=7,
        sha256="a" * 64,
        kind="task_input",
        provenance=ResourceProvenance(source="user_upload", created_at="2026-07-30T00:00:00Z"),
    )
    second = SessionFile(
        id="asset_" + "b" * 32,
        originalName="hidden.txt",
        storedName="b" * 32 + ".txt",
        relativePath="inputs/files/" + "b" * 32 + ".txt",
        mimeType="text/plain",
        mediaKind="text",
        size=6,
        sha256="b" * 64,
        kind="task_input",
        provenance=ResourceProvenance(source="user_upload", created_at="2026-07-30T00:00:00Z"),
    )
    task = _task(task_id="serial_scoped_inputs").model_copy(update={
        "session_input": SessionInput(prompt="scoped", files=[first, second])
    })
    bundle, orchestrator = _orchestrator(tmp_path, task)
    try:
        state = orchestrator.bootstrap()
        orchestrator.create_intent(
            supervisor_solver_id=state.supervisor_solver_id,
            kind="validation",
            title="Scoped worker",
            objective="inspect one assigned input",
            allowed_resource_ids=(first.resource_id,),
            priority=10,
        )
        assignment = orchestrator.dispatch_next()
        assert assignment is not None
        context = SessionContextBuilder(
            task=task,
            workspace=tmp_path / "workspace",
            supports_vision=False,
            allowed_resource_ids=assignment.allowed_resources,
            task_spec=bundle.tasks.get_task_spec(task.id),
        ).markdown()
        assert "allowed.txt" in context
        assert "hidden.txt" not in context

        solver = bundle.solvers.get_solver(assignment.solver_id)
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        catalog = empty_mcp_catalog()
        manifest = ToolManifestBuilder().build(
            task=task, solver=solver, definition=definition,
            intent=assignment.intent, catalog=catalog,
        )

        class Adapter:
            calls = 0

            def execute(self, action):
                self.calls += 1
                return RawExecutionResult(action_id=action.id, status="succeeded", output={"ok": True})

        adapter = Adapter()
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            execution_adapter=adapter,
            control_handlers=orchestrator.gateway_control_handlers(solver.id),
            allowed_resource_ids=assignment.allowed_resources,
        )
        response = gateway.handle(ToolRequest(
            provider_tool_name="input_get",
            arguments={"input_id": second.id},
            model_intent=ModelToolIntent(rationale="inspect hidden input"),
            action_context=ActionContext(
                task_id=task.id,
                solver_id=solver.id,
                intent_id=assignment.intent_id,
                local_plan_step_id=f"local_step_{solver.id}_1",
                orchestration_role="worker",
                solver_definition_id=solver.definition_id,
                execution_policy_snapshot_id="execution:" + "a" * 64,
                solver_capability_snapshot_id="tool:" + "b" * 64,
                attempt=1,
                created_at="2026-07-30T00:00:00Z",
            ),
            tool_call_id="call_hidden_input",
        ))
        assert response.error and response.error.code == "RESOURCE_NOT_OWNED"
        assert adapter.calls == 0
    finally:
        bundle.close()
