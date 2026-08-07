from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.capability_fixtures import capability_binding, empty_mcp_catalog
from tests.runtime_fixtures import task as v6_task
from tga.contracts import ActionEffect, HighImpactExecutionPolicy, TGATask
from tga.infrastructure.persistence import PersistenceBundle
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.runtime.orchestration import TaskOrchestrator
from tga.runtime.tooling.catalog import RuntimeToolCatalog
from tga.tools.mcp_registry import MCPCatalogSnapshot, MCPToolRoute
from tga.runtime.tooling.catalog.manifest_builder import ToolManifestBuilder
from tga.runtime.tooling import ToolDefinitionBuilder
from tga.runtime.tooling.governance import (
    BudgetService,
    IdempotencyService,
    ResourceLockService,
    SemanticRepeatGuard,
)
from tga.runtime.tooling.lifecycle import ActionTransitionError
from tga.runtime.tooling.requests import (
    ActionContext,
    AuthorizationDecision,
    GovernedAction,
    ModelToolIntent,
    ToolRequest,
)
from tga.runtime.tooling.results import RawExecutionResult
from tga.runtime.tooling.routing import ToolGovernanceGateway


NOW = "2026-07-30T00:00:00Z"


def _task(task_id: str = "tool_governance") -> TGATask:
    task = v6_task(id=task_id, name="governance", mode="ctf", goal="govern tools")
    return task.model_copy(update={
        "execution_policy": task.execution_policy.model_copy(update={
            "local_compute": task.execution_policy.local_compute.model_copy(
                update={"mode": "isolated"}
            ),
            "high_impact": HighImpactExecutionPolicy(
                mode="allowlisted",
                allowed_actions=["artifact.publish", "input.materialize"],
            )
        })
    })


def _solver(definition_id: str, *_capabilities: str, profile: str = "test"):
    del profile
    definition = SolverDefinitionRegistry.builtin().require(definition_id)
    return SimpleNamespace(
        id=f"solver_{definition_id}",
        task_id="tool_governance",
        definition_id=definition.id,
        orchestration_role=definition.orchestration_role,
        specialties=definition.specialties,
        completion_authority=definition.completion_authority,
        capability_binding_snapshot=capability_binding(definition),
    ), definition


def _catalog(task: TGATask, definition_id: str = "ctf-web-solver") -> RuntimeToolCatalog:
    del task, definition_id
    return empty_mcp_catalog()


def _context(task_id: str = "tool_governance", *, solver=None) -> ActionContext:
    solver_id = solver.id if solver is not None else "solver_main"
    role = str(solver.orchestration_role) if solver is not None else "supervisor"
    definition_id = solver.definition_id if solver is not None else "ctf-supervisor"
    intent_id = solver.assigned_intent_id if solver is not None else f"intent_initial_{task_id}"
    return ActionContext(
        task_id=task_id,
        solver_id=solver_id,
        intent_id=intent_id,
        local_plan_step_id=f"local_step_{solver_id}_1" if intent_id else None,
        orchestration_role=role,
        solver_definition_id=definition_id,
        execution_policy_snapshot_id="execution:" + "a" * 64,
        solver_capability_snapshot_id="tool:" + "b" * 64,
        skill_snapshot_id=None,
        attempt=1,
        created_at=NOW,
    )


def _action(
    *, action_id: str = "governed_one", capability: str = "artifact.publish",
    idempotency_key: str | None = None, lock_key: str | None = None,
    context: ActionContext | None = None,
) -> GovernedAction:
    context = context or _context()
    return GovernedAction(
        id=action_id,
        context=context,
        provider_tool_name="artifact_publish",
        tool_call_id=f"call_{action_id}",
        tool_class="execution",
        capability=capability,
        normalized_arguments={"relative_path": "report.txt", "label": "result"},
        resolved_target=f"workspace:{context.solver_id}:report.txt",
        risk="active",
        effect=ActionEffect(
            scope="workspace", persistence="persistent", reversibility="reversible",
            category="file_write", description="Write the solver report.",
        ),
        authorization=AuthorizationDecision(allowed=True, reason="test"),
        idempotency_key=idempotency_key,
        semantic_fingerprint="c" * 64,
        resource_lock_key=lock_key,
        status="proposed",
        created_at=NOW,
        updated_at=NOW,
    )


def _bootstrap_supervisor(bundle: PersistenceBundle, task: TGATask):
    orchestrator = TaskOrchestrator(task=task, repositories=bundle)
    state = orchestrator.bootstrap()
    assert state.supervisor_solver_id is not None
    solver = bundle.solvers.get_solver(state.supervisor_solver_id)
    assert solver is not None
    return orchestrator, solver


def _dispatch_worker(
    bundle: PersistenceBundle, task: TGATask, *, intent_kind: str = "challenge_classification"
):
    orchestrator, _ = _bootstrap_supervisor(bundle, task)
    if intent_kind != "challenge_classification":
        plan = bundle.plans.get_global_plan(task.id)
        assert plan is not None and len(plan.intents) == 1
        intent = plan.intents[0].model_copy(update={"kind": intent_kind})
        bundle.plans.compare_and_swap_global_plan(
            plan.model_copy(update={
                "version": plan.version + 1, "intents": [intent],
            }),
            expected_version=plan.version,
        )
    assignment = orchestrator.dispatch_next()
    assert assignment is not None
    solver = bundle.solvers.get_solver(assignment.solver_id)
    assert solver is not None and solver.orchestration_role == "worker"
    return orchestrator, solver


def test_model_intent_forbids_authoritative_ids_and_action_context_is_host_owned() -> None:
    with pytest.raises(ValidationError):
        ModelToolIntent.model_validate({"rationale": "inspect", "solver_id": "forged"})

    context = _context()
    request = ToolRequest(
        provider_tool_name="input_read",
        arguments={"input_id": "asset_1"},
        model_intent=ModelToolIntent(rationale="inspect"),
        action_context=context,
        tool_call_id="call_one",
    )
    assert request.action_context.solver_id == "solver_main"
    assert "solver_id" not in request.arguments


def test_role_manifests_are_distinct_and_respect_completion_authority() -> None:
    task = _task()
    catalog = _catalog(task)
    all_capabilities = tuple(item.capability for item in catalog.entries)
    builder = ToolManifestBuilder()

    supervisor, supervisor_definition = _solver("ctf-supervisor", *all_capabilities)
    worker, worker_definition = _solver("ctf-web-solver", *all_capabilities)
    reviewer, reviewer_definition = _solver("evidence-reviewer", *all_capabilities)
    reporter, reporter_definition = _solver("security-reporter", *all_capabilities)

    supervisor_names = set(builder.build(
        task=task, solver=supervisor, definition=supervisor_definition,
        intent=None, catalog=catalog,
    ).provider_names)
    worker_names = set(builder.build(
        task=task, solver=worker, definition=worker_definition,
        intent=None, catalog=catalog,
    ).provider_names)
    reviewer_names = set(builder.build(
        task=task, solver=reviewer, definition=reviewer_definition,
        intent=None, catalog=catalog,
    ).provider_names)
    reporter_names = set(builder.build(
        task=task, solver=reporter, definition=reporter_definition,
        intent=None, catalog=catalog,
    ).provider_names)

    assert "propose_task_completion" in supervisor_names
    assert "confirm_finding" in supervisor_names
    assert "submit_worker_result" not in supervisor_names
    assert "kali_exec" not in supervisor_names
    assert "submit_worker_result" in worker_names
    assert "propose_task_completion" not in worker_names
    assert {"artifact.inspect", "review_evidence", "review_finding"} <= {
        item.capability for item in builder.build(
            task=task, solver=reviewer, definition=reviewer_definition,
            intent=None, catalog=catalog,
        ).entries
    }
    assert "propose_task_completion" not in reviewer_names
    assert "propose_task_completion" not in reporter_names
    assert not {"kali_exec", "kali_session"} & reporter_names


def test_supervisor_manifest_constrains_model_authored_intent_kinds() -> None:
    task = _task("intent_schema")
    catalog = _catalog(task)
    all_capabilities = tuple(item.capability for item in catalog.entries)
    supervisor, definition = _solver("ctf-supervisor", *all_capabilities)

    manifest = ToolManifestBuilder().build(
        task=task,
        solver=supervisor,
        definition=definition,
        intent=None,
        catalog=catalog,
        supported_intent_kinds=("challenge_classification", "ctf_web"),
    )

    for capability_id in ("create_intent", "update_global_plan"):
        capability = next(
            item for item in manifest.host_capabilities if item.id == capability_id
        )
        assert capability.input_schema["properties"]["kind"]["enum"] == [
            "challenge_classification", "ctf_web"
        ]


def test_runtime_catalog_hides_mcp_tools_blocked_by_frozen_policy() -> None:
    snapshot = MCPCatalogSnapshot(
        version="mcp_test",
        routes=(MCPToolRoute(
            provider_name="mcp__test__run",
            server_id="test",
            method="run",
        ),),
    )
    disabled = _task().execution_policy.model_copy(update={
        "local_compute": _task().execution_policy.local_compute.model_copy(
            update={"mode": "disabled"}
        ),
        "high_impact": HighImpactExecutionPolicy(mode="forbidden"),
    })
    visible = RuntimeToolCatalog.from_runtime(
        mcp_snapshot=snapshot,
        execution_policy=disabled,
        mcp_risks={("test", "run"): "active"},
    )
    hidden_destructive = RuntimeToolCatalog.from_runtime(
        mcp_snapshot=snapshot,
        execution_policy=disabled,
        mcp_risks={("test", "run"): "destructive"},
    )

    assert visible.entries == ()
    assert hidden_destructive.entries == ()


def test_manifest_schema_exposes_only_non_authoritative_model_governance() -> None:
    task = _task()
    catalog = _catalog(task)
    capabilities = tuple(item.capability for item in catalog.entries)
    solver, definition = _solver(
        "ctf-supervisor", *capabilities,
        profile="test",
    )
    manifest = ToolManifestBuilder().build(
        task=task, solver=solver, definition=definition, intent=None, catalog=catalog,
    )

    schemas = ToolDefinitionBuilder(manifest=manifest).build()
    governance = schemas[0]["function"]["parameters"]["properties"]["_tga"]

    assert set(governance["properties"]) == {
        "rationale", "expected_outcome", "retry_reason",
        "alternative_analysis", "proposed_effect",
    }
    encoded = str(governance)
    assert "task_id" not in encoded
    assert "solver_id" not in encoded
    assert "intent_id" not in encoded
    assert "strategy_card_id" not in encoded


def test_action_state_machine_rejects_terminal_reentry_and_uses_expected_status(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        _, solver = _dispatch_worker(bundle, task)
        repository = bundle.tool_governance
        repository.add_action(_action(context=_context(solver=solver)))
        repository.transition("governed_one", "validated", expected_status="proposed")
        repository.transition("governed_one", "queued", expected_status="validated")
        repository.transition("governed_one", "running", expected_status="queued")
        repository.transition("governed_one", "succeeded", expected_status="running")

        with pytest.raises(ActionTransitionError):
            repository.transition("governed_one", "running", expected_status="succeeded")
        with pytest.raises(ActionTransitionError):
            repository.transition("governed_one", "failed", expected_status="running")
    finally:
        bundle.close()


def test_semantic_repeat_idempotency_resource_lock_and_budget_are_distinct(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        _, solver = _dispatch_worker(bundle, task)
        context = _context(solver=solver)
        repository = bundle.tool_governance
        first = _action(
            action_id="governed_first", idempotency_key="idem_same",
            lock_key=f"workspace:{solver.id}:report.txt", context=context,
        )
        repository.add_action(first)
        repository.transition(first.id, "validated", expected_status="proposed")

        repeat = SemanticRepeatGuard(repository).check(
            _action(action_id="governed_repeat", idempotency_key="different", context=context)
        )
        assert repeat.previous_action_id == first.id
        assert repeat.requires_retry_reason is True

        idempotency = IdempotencyService(repository)
        assert idempotency.reserve(first).created is True
        duplicate = idempotency.reserve(
            _action(action_id="governed_duplicate", idempotency_key="idem_same", context=context)
        )
        assert duplicate.created is False
        assert duplicate.action_id == first.id

        lookup = idempotency.lookup(
            _action(action_id="governed_lookup", idempotency_key="idem_same", context=context)
        )
        assert lookup is not None
        assert lookup.action_id == first.id

        locks = ResourceLockService(repository)
        assert locks.acquire(first, ttl_seconds=30) is True
        assert locks.acquire(
            _action(action_id="governed_other", lock_key=first.resource_lock_key, context=context),
            ttl_seconds=30,
        ) is False
        locks.release(first)

        budgets = BudgetService(repository)
        reservation = budgets.reserve(first, tool_calls=1, artifacts=0)
        assert reservation.status == "reserved"
        settled = budgets.settle(reservation.id, artifacts=1)
        assert settled.status == "settled"
    finally:
        bundle.close()


def test_task_budget_is_a_persistent_hard_upper_bound(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task().model_copy(update={
        "execution_budget": {"max_tool_calls": 0, "max_artifacts": 10}
    })
    try:
        bundle.tasks.create_task(task)
        _, solver = _dispatch_worker(bundle, task)

        with pytest.raises(PersistenceConflict, match="TaskBudget tool-call limit"):
            BudgetService(bundle.tool_governance).reserve(
                _action(action_id="governed_task_budget", context=_context(solver=solver)),
                tool_calls=1,
                artifacts=0,
            )
    finally:
        bundle.close()


class _ExecutionAdapter:
    def __init__(self) -> None:
        self.calls: list[GovernedAction] = []

    def execute(self, action: GovernedAction) -> RawExecutionResult:
        self.calls.append(action)
        return RawExecutionResult(
            action_id=action.id,
            status="succeeded",
            output={"ok": True, "summary": "legacy execution preserved"},
            artifact_ids=[],
            telemetry={"adapter": "test"},
        )


class _ExplodingExecutionAdapter(_ExecutionAdapter):
    def execute(self, action: GovernedAction) -> RawExecutionResult:
        raise RuntimeError("adapter exploded")


def test_gateway_rejects_forged_ids_routes_control_without_execution_and_bounds_reads(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        _, solver = _bootstrap_supervisor(bundle, task)
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        catalog = _catalog(task)
        manifest = ToolManifestBuilder().build(
            task=task, solver=solver, definition=definition, intent=None, catalog=catalog,
        )
        adapter = _ExecutionAdapter()
        completion_calls: list[dict] = []
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
                execution_adapter=adapter,
            control_handlers={
                "propose_task_completion": lambda arguments: completion_calls.append(arguments)
                or {"ok": True, "terminal": True, "summary": "done"}
            },
        )

        forged = gateway.handle(ToolRequest(
            provider_tool_name="input_read",
            arguments={"input_id": "asset_1", "solver_id": "forged"},
            model_intent=ModelToolIntent(rationale="inspect"),
            action_context=_context(solver=solver),
            tool_call_id="call_forged",
        ))
        assert forged.error and forged.error.code == "AUTHORITATIVE_ARGUMENT_FORBIDDEN"
        assert adapter.calls == []

        nested_forged = gateway.handle(ToolRequest(
            provider_tool_name="input_read",
            arguments={"input_id": "asset_1", "metadata": {"intent_id": "forged"}},
            model_intent=ModelToolIntent(rationale="inspect"),
            action_context=_context(solver=solver),
            tool_call_id="call_nested_forged",
        ))
        assert nested_forged.error
        assert nested_forged.error.code == "AUTHORITATIVE_ARGUMENT_FORBIDDEN"
        assert adapter.calls == []

        oversized = gateway.handle(ToolRequest(
            provider_tool_name="input_read",
            arguments={"input_id": "asset_1", "limit": 262145},
            model_intent=ModelToolIntent(rationale="inspect"),
            action_context=_context(solver=solver),
            tool_call_id="call_large",
        ))
        assert oversized.error and oversized.error.code == "RESOURCE_READ_LIMIT_EXCEEDED"
        assert adapter.calls == []

        foreign_resource = gateway.handle(ToolRequest(
            provider_tool_name="input_read",
            arguments={"input_id": "asset_not_owned", "limit": 64},
            model_intent=ModelToolIntent(rationale="inspect"),
            action_context=_context(solver=solver),
            tool_call_id="call_foreign_input",
        ))
        assert foreign_resource.error
        assert foreign_resource.error.code == "RESOURCE_NOT_OWNED"
        assert adapter.calls == []

        completed = gateway.handle(ToolRequest(
            provider_tool_name="propose_task_completion",
            arguments={"summary": "done"},
            model_intent=ModelToolIntent(rationale="complete"),
            action_context=_context(solver=solver),
            tool_call_id="call_complete",
        ))
        assert completed.status == "succeeded"
        assert completion_calls == [{"summary": "done"}]
        assert adapter.calls == []
    finally:
        bundle.close()


def test_gateway_executes_allowed_tool_through_adapter_and_persists_terminal_state(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        _, solver = _dispatch_worker(bundle, task, intent_kind="binary_analysis")
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        catalog = _catalog(task)
        intent = bundle.plans.get_global_plan(task.id).intents[0]
        manifest = ToolManifestBuilder().build(
            task=task, solver=solver, definition=definition, intent=intent, catalog=catalog,
        )
        adapter = _ExecutionAdapter()
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            execution_adapter=adapter,
        )

        result = gateway.handle(ToolRequest(
            provider_tool_name="artifact_publish",
            arguments={"relative_path": "report.txt", "label": "result"},
            model_intent=ModelToolIntent(
                rationale="persist the report",
                expected_outcome="report exists",
            ),
            action_context=_context(solver=solver),
            tool_call_id="call_execute",
        ))

        assert result.status == "succeeded"
        assert len(adapter.calls) == 1
        persisted = bundle.tool_governance.find_by_tool_call(
            task.id, solver.id, "call_execute"
        )
        assert persisted is not None
        assert persisted["status"] == "succeeded"
        assert persisted["result"]["telemetry"]["adapter"] == "test"
        reservation = bundle.database.conn.execute(
            "SELECT status FROM tool_budget_reservations WHERE action_id=?",
            (persisted["id"],),
        ).fetchone()
        assert reservation["status"] == "settled"
        transitions = bundle.database.conn.execute(
            "SELECT to_status FROM governed_action_transitions WHERE action_id=? ORDER BY seq",
            (persisted["id"],),
        ).fetchall()
        assert [row["to_status"] for row in transitions] == [
            "proposed", "validated", "queued", "running", "succeeded",
        ]
    finally:
        bundle.close()


def test_gateway_rejects_high_impact_host_tool_from_stale_manifest(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    allowed_task = _task("stale_manifest_policy")
    forbidden_task = allowed_task.model_copy(update={
        "execution_policy": allowed_task.execution_policy.model_copy(update={
            "high_impact": HighImpactExecutionPolicy(mode="forbidden")
        })
    })
    try:
        bundle.tasks.create_task(forbidden_task)
        _, solver = _dispatch_worker(
            bundle, forbidden_task, intent_kind="binary_analysis"
        )
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        intent = bundle.plans.get_global_plan(forbidden_task.id).intents[0]
        stale_solver = solver.model_copy(update={
            "execution_policy_snapshot": allowed_task.execution_policy,
        })
        stale_manifest = ToolManifestBuilder().build(
            task=allowed_task,
            solver=stale_solver,
            definition=definition,
            intent=intent,
            catalog=_catalog(allowed_task),
        )
        gateway = ToolGovernanceGateway(
            task=forbidden_task,
            execution_policy=forbidden_task.execution_policy,
            manifest=stale_manifest,
            repository=bundle.tool_governance,
            execution_adapter=_ExecutionAdapter(),
        )

        result = gateway.handle(ToolRequest(
            provider_tool_name="artifact_publish",
            arguments={"relative_path": "report.txt", "label": "result"},
            model_intent=ModelToolIntent(rationale="attempt stale capability"),
            action_context=_context(task_id=forbidden_task.id, solver=solver),
            tool_call_id="call_stale_manifest",
        ))

        assert result.error and result.error.code == "HIGH_IMPACT_FORBIDDEN"
    finally:
        bundle.close()


def test_high_impact_action_recovery_replays_without_duplicate_execution(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        _, solver = _dispatch_worker(bundle, task, intent_kind="binary_analysis")
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        intent = bundle.plans.get_global_plan(task.id).intents[0]
        manifest = ToolManifestBuilder().build(
            task=task,
            solver=solver,
            definition=definition,
            intent=intent,
            catalog=_catalog(task),
        )
        first_adapter = _ExecutionAdapter()
        first_gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            execution_adapter=first_adapter,
        )
        arguments = {"relative_path": "report.txt", "label": "result"}
        first = first_gateway.handle(ToolRequest(
            provider_tool_name="artifact_publish",
            arguments=arguments,
            model_intent=ModelToolIntent(rationale="publish once"),
            action_context=_context(solver=solver),
            tool_call_id="call_before_restart",
        ))
        assert first.status == "succeeded" and len(first_adapter.calls) == 1

        recovered_adapter = _ExecutionAdapter()
        recovered_gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
                execution_adapter=recovered_adapter,
        )
        replay = recovered_gateway.handle(ToolRequest(
            provider_tool_name="artifact_publish",
            arguments=arguments,
            model_intent=ModelToolIntent(rationale="recover the same operation"),
            action_context=_context(solver=solver),
            tool_call_id="call_after_restart",
        ))
        assert replay.status == "succeeded"
        assert replay.idempotent_replay is True
        assert recovered_adapter.calls == []

        retry = recovered_gateway.handle(ToolRequest(
            provider_tool_name="artifact_publish",
            arguments=arguments,
            model_intent=ModelToolIntent(
                rationale="explicit retry",
                retry_reason="a new attempt was explicitly requested",
            ),
            action_context=_context(solver=solver).model_copy(update={"attempt": 2}),
            tool_call_id="call_retry_attempt_two",
        ))
        assert retry.status == "succeeded"
        assert len(recovered_adapter.calls) == 1
        assert recovered_adapter.calls[0].attempt == 2
        assert recovered_adapter.calls[0].idempotency_key != first_adapter.calls[0].idempotency_key
    finally:
        bundle.close()


def test_gateway_discards_result_if_runner_lease_is_lost_during_execution(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        _, solver = _dispatch_worker(bundle, task, intent_kind="binary_analysis")
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        intent = bundle.plans.get_global_plan(task.id).intents[0]
        manifest = ToolManifestBuilder().build(
            task=task,
            solver=solver,
            definition=definition,
            intent=intent,
            catalog=_catalog(task),
        )
        adapter = _ExecutionAdapter()
        lease_checks = iter((True, False))
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            execution_adapter=adapter,
            lease_validator=lambda: next(lease_checks),
        )

        result = gateway.handle(ToolRequest(
            provider_tool_name="artifact_publish",
            arguments={"relative_path": "late.txt", "label": "late"},
            model_intent=ModelToolIntent(rationale="test lease fencing"),
            action_context=_context(solver=solver),
            tool_call_id="call_lease_lost",
        ))

        assert result.error and result.error.code == "RUNNER_LEASE_LOST"
        assert len(adapter.calls) == 1
        persisted = bundle.tool_governance.find_by_tool_call(
            task.id, solver.id, "call_lease_lost"
        )
        assert persisted["status"] == "cancelled"
        assert persisted.get("result") is None
        reservation = bundle.database.conn.execute(
            "SELECT status FROM tool_budget_reservations WHERE action_id=?",
            (persisted["id"],),
        ).fetchone()
        assert reservation["status"] == "released"
    finally:
        bundle.close()


def test_retrieval_tool_routes_through_gateway_and_persists_auditable_action(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        _, solver = _bootstrap_supervisor(bundle, task)
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        manifest = ToolManifestBuilder().build(
            task=task,
            solver=solver,
            definition=definition,
            intent=None,
            catalog=_catalog(task),
        )
        assert "retrieval_search" in manifest.provider_names
        adapter = _ExecutionAdapter()
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            execution_adapter=adapter,
            retrieval_handlers={
                "retrieval.search": lambda arguments: {
                    "ok": True,
                    "retrieval_run_id": "run_audit",
                    "query": arguments["query"],
                    "items": [],
                }
            },
        )

        result = gateway.handle(ToolRequest(
            provider_tool_name="retrieval_search",
            arguments={"query": "bounded reference"},
            model_intent=ModelToolIntent(rationale="find relevant reference material"),
            action_context=_context(solver=solver),
            tool_call_id="call_retrieval",
        ))

        assert result.status == "succeeded"
        assert result.model_payload["retrieval_run_id"] == "run_audit"
        assert adapter.calls == []
        persisted = bundle.tool_governance.find_by_tool_call(
            task.id, solver.id, "call_retrieval"
        )
        assert persisted["tool_class"] == "retrieval"
        assert persisted["capability"] == "retrieval.search"
    finally:
        bundle.close()


def test_gateway_maps_adapter_exception_to_failed_raw_result(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        _, solver = _dispatch_worker(bundle, task, intent_kind="binary_analysis")
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        intent = bundle.plans.get_global_plan(task.id).intents[0]
        manifest = ToolManifestBuilder().build(
            task=task,
            solver=solver,
            definition=definition,
            intent=intent,
            catalog=_catalog(task),
        )
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            execution_adapter=_ExplodingExecutionAdapter(),
        )

        result = gateway.handle(ToolRequest(
            provider_tool_name="artifact_publish",
            arguments={"relative_path": "report.txt", "label": "result"},
            model_intent=ModelToolIntent(rationale="persist the report"),
            action_context=_context(solver=solver),
            tool_call_id="call_explodes",
        ))

        assert result.status == "failed"
        assert result.error and result.error.code == "EXECUTION_ADAPTER_ERROR"
        persisted = bundle.tool_governance.find_by_tool_call(
            task.id, solver.id, "call_explodes"
        )
        assert persisted["status"] == "failed"
        assert persisted["result"]["error"]["code"] == "EXECUTION_ADAPTER_ERROR"
    finally:
        bundle.close()
