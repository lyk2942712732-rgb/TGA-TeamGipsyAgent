from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tga.capabilities.registry import build_default_registry
from tga.contracts import ActionEffect, TGATask
from tga.domain.solver.instances import ToolPolicySnapshot
from tga.infrastructure.persistence import PersistenceBundle
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.runtime.agents.single_solver_adapter import SingleSolverProvisioner
from tga.runtime.tooling.catalog import RuntimeToolCatalog
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
    return TGATask(id=task_id, name="governance", mode="ctf", goal="govern tools")


def _policy(*capabilities: str, profile: str = "test") -> ToolPolicySnapshot:
    payload = "\0".join(capabilities).encode()
    return ToolPolicySnapshot(
        profile=profile,
        allowed_tool_groups=("control", "resource_read", "execution", "retrieval"),
        allowed_capabilities=tuple(capabilities),
        content_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _solver(definition_id: str, *capabilities: str, profile: str = "test"):
    definition = SolverDefinitionRegistry.builtin().require(definition_id)
    return SimpleNamespace(
        id=f"solver_{definition_id}",
        task_id="tool_governance",
        definition_id=definition.id,
        orchestration_role=definition.orchestration_role,
        specialties=definition.specialties,
        completion_authority=definition.completion_authority,
        tool_policy_snapshot=_policy(*capabilities, profile=profile),
    ), definition


def _catalog(task: TGATask) -> RuntimeToolCatalog:
    registry = build_default_registry()
    tool_names = {
        f"tga_{item['name'].replace('.', '_')}": item["name"]
        for item in registry.snapshot()["capabilities"]
    }
    return RuntimeToolCatalog.from_runtime(
        task=task,
        registry=registry,
        tool_names=tool_names,
        mcp_snapshot=SimpleNamespace(function_tools=lambda: [], routes={}),
    )


def _context(task_id: str = "tool_governance") -> ActionContext:
    return ActionContext(
        task_id=task_id,
        solver_id="solver_main",
        intent_id=f"intent_initial_{task_id}",
        local_plan_step_id="local_step_solver_main_1",
        orchestration_role="supervisor",
        solver_definition_id="task-supervisor",
        execution_policy_snapshot_id="execution:" + "a" * 64,
        solver_tool_policy_snapshot_id="tool:" + "b" * 64,
        skill_snapshot_id=None,
        attempt=1,
        created_at=NOW,
    )


def _action(
    *, action_id: str = "governed_one", capability: str = "workspace.write",
    idempotency_key: str | None = None, lock_key: str | None = None,
) -> GovernedAction:
    return GovernedAction(
        id=action_id,
        context=_context(),
        provider_tool_name="tga_workspace_write",
        tool_call_id=f"call_{action_id}",
        tool_class="execution",
        capability=capability,
        normalized_arguments={"relative_path": "report.txt", "content": "result"},
        resolved_target="workspace:solver_main:report.txt",
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

    supervisor, supervisor_definition = _solver("task-supervisor", *all_capabilities)
    worker, worker_definition = _solver("web-network-analyst", *all_capabilities)
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
    assert "tga_workspace_python" not in supervisor_names
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
    assert not {"tga_workspace_python", "tga_workspace_shell", "tga_http_request"} & reporter_names


def test_manifest_schema_exposes_only_non_authoritative_model_governance() -> None:
    task = _task()
    catalog = _catalog(task)
    capabilities = tuple(item.capability for item in catalog.entries)
    solver, definition = _solver(
        "task-supervisor", *capabilities,
        profile="phase5-single-solver-compatibility",
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
        SingleSolverProvisioner(bundle).ensure(task=task, solver_id="solver_main")
        repository = bundle.tool_governance
        repository.add_action(_action())
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
        SingleSolverProvisioner(bundle).ensure(task=task, solver_id="solver_main")
        repository = bundle.tool_governance
        first = _action(
            action_id="governed_first", idempotency_key="idem_same",
            lock_key="workspace:solver_main:report.txt",
        )
        repository.add_action(first)
        repository.transition(first.id, "validated", expected_status="proposed")

        repeat = SemanticRepeatGuard(repository).check(
            _action(action_id="governed_repeat", idempotency_key="different")
        )
        assert repeat.previous_action_id == first.id
        assert repeat.requires_retry_reason is True

        idempotency = IdempotencyService(repository)
        assert idempotency.reserve(first).created is True
        duplicate = idempotency.reserve(
            _action(action_id="governed_duplicate", idempotency_key="idem_same")
        )
        assert duplicate.created is False
        assert duplicate.action_id == first.id

        lookup = idempotency.lookup(
            _action(action_id="governed_lookup", idempotency_key="idem_same")
        )
        assert lookup is not None
        assert lookup.action_id == first.id

        locks = ResourceLockService(repository)
        assert locks.acquire(first, ttl_seconds=30) is True
        assert locks.acquire(
            _action(action_id="governed_other", lock_key=first.resource_lock_key),
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
        SingleSolverProvisioner(bundle).ensure(task=task, solver_id="solver_main")

        with pytest.raises(PersistenceConflict, match="TaskBudget tool-call limit"):
            BudgetService(bundle.tool_governance).reserve(
                _action(action_id="governed_task_budget"),
                tool_calls=1,
                artifacts=0,
            )
    finally:
        bundle.close()


class _LegacyAdapter:
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


class _ExplodingLegacyAdapter(_LegacyAdapter):
    def execute(self, action: GovernedAction) -> RawExecutionResult:
        raise RuntimeError("adapter exploded")


def test_gateway_rejects_forged_ids_routes_control_without_execution_and_bounds_reads(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        solver = SingleSolverProvisioner(bundle).ensure(task=task, solver_id="solver_main")
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        catalog = _catalog(task)
        intent = bundle.plans.get_global_plan(task.id).intents[0]
        manifest = ToolManifestBuilder().build(
            task=task, solver=solver, definition=definition, intent=intent, catalog=catalog,
        )
        adapter = _LegacyAdapter()
        completion_calls: list[dict] = []
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            legacy_adapter=adapter,
            control_handlers={
                "propose_task_completion": lambda arguments: completion_calls.append(arguments)
                or {"ok": True, "terminal": True, "summary": "done"}
            },
        )

        forged = gateway.handle(ToolRequest(
            provider_tool_name="input_read",
            arguments={"input_id": "asset_1", "solver_id": "forged"},
            model_intent=ModelToolIntent(rationale="inspect"),
            action_context=_context(),
            tool_call_id="call_forged",
        ))
        assert forged.error and forged.error.code == "AUTHORITATIVE_ARGUMENT_FORBIDDEN"
        assert adapter.calls == []

        nested_forged = gateway.handle(ToolRequest(
            provider_tool_name="input_read",
            arguments={"input_id": "asset_1", "metadata": {"intent_id": "forged"}},
            model_intent=ModelToolIntent(rationale="inspect"),
            action_context=_context(),
            tool_call_id="call_nested_forged",
        ))
        assert nested_forged.error
        assert nested_forged.error.code == "AUTHORITATIVE_ARGUMENT_FORBIDDEN"
        assert adapter.calls == []

        oversized = gateway.handle(ToolRequest(
            provider_tool_name="input_read",
            arguments={"input_id": "asset_1", "limit": 262145},
            model_intent=ModelToolIntent(rationale="inspect"),
            action_context=_context(),
            tool_call_id="call_large",
        ))
        assert oversized.error and oversized.error.code == "RESOURCE_READ_LIMIT_EXCEEDED"
        assert adapter.calls == []

        foreign_resource = gateway.handle(ToolRequest(
            provider_tool_name="input_read",
            arguments={"input_id": "asset_not_owned", "limit": 64},
            model_intent=ModelToolIntent(rationale="inspect"),
            action_context=_context(),
            tool_call_id="call_foreign_input",
        ))
        assert foreign_resource.error
        assert foreign_resource.error.code == "RESOURCE_NOT_OWNED"
        assert adapter.calls == []

        completed = gateway.handle(ToolRequest(
            provider_tool_name="propose_task_completion",
            arguments={"summary": "done"},
            model_intent=ModelToolIntent(rationale="complete"),
            action_context=_context(),
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
        solver = SingleSolverProvisioner(bundle).ensure(task=task, solver_id="solver_main")
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        catalog = _catalog(task)
        intent = bundle.plans.get_global_plan(task.id).intents[0]
        manifest = ToolManifestBuilder().build(
            task=task, solver=solver, definition=definition, intent=intent, catalog=catalog,
        )
        adapter = _LegacyAdapter()
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            legacy_adapter=adapter,
        )

        result = gateway.handle(ToolRequest(
            provider_tool_name="tga_workspace_write",
            arguments={"relative_path": "report.txt", "content": "result"},
            model_intent=ModelToolIntent(
                rationale="persist the report",
                expected_outcome="report exists",
            ),
            action_context=_context(),
            tool_call_id="call_execute",
        ))

        assert result.status == "succeeded"
        assert len(adapter.calls) == 1
        persisted = bundle.tool_governance.find_by_tool_call(
            task.id, "solver_main", "call_execute"
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


def test_high_impact_action_recovery_replays_without_duplicate_execution(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = _task()
    try:
        bundle.tasks.create_task(task)
        solver = SingleSolverProvisioner(bundle).ensure(
            task=task, solver_id="solver_main"
        )
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        intent = bundle.plans.get_global_plan(task.id).intents[0]
        manifest = ToolManifestBuilder().build(
            task=task,
            solver=solver,
            definition=definition,
            intent=intent,
            catalog=_catalog(task),
        )
        first_adapter = _LegacyAdapter()
        first_gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            legacy_adapter=first_adapter,
        )
        arguments = {"relative_path": "report.txt", "content": "result"}
        first = first_gateway.handle(ToolRequest(
            provider_tool_name="tga_workspace_write",
            arguments=arguments,
            model_intent=ModelToolIntent(rationale="publish once"),
            action_context=_context(),
            tool_call_id="call_before_restart",
        ))
        assert first.status == "succeeded" and len(first_adapter.calls) == 1

        recovered_adapter = _LegacyAdapter()
        recovered_gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            legacy_adapter=recovered_adapter,
        )
        replay = recovered_gateway.handle(ToolRequest(
            provider_tool_name="tga_workspace_write",
            arguments=arguments,
            model_intent=ModelToolIntent(rationale="recover the same operation"),
            action_context=_context(),
            tool_call_id="call_after_restart",
        ))
        assert replay.status == "succeeded"
        assert replay.idempotent_replay is True
        assert recovered_adapter.calls == []

        retry = recovered_gateway.handle(ToolRequest(
            provider_tool_name="tga_workspace_write",
            arguments=arguments,
            model_intent=ModelToolIntent(
                rationale="explicit retry",
                retry_reason="a new attempt was explicitly requested",
            ),
            action_context=_context().model_copy(update={"attempt": 2}),
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
        solver = SingleSolverProvisioner(bundle).ensure(
            task=task, solver_id="solver_main"
        )
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        intent = bundle.plans.get_global_plan(task.id).intents[0]
        manifest = ToolManifestBuilder().build(
            task=task,
            solver=solver,
            definition=definition,
            intent=intent,
            catalog=_catalog(task),
        )
        adapter = _LegacyAdapter()
        lease_checks = iter((True, False))
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            legacy_adapter=adapter,
            lease_validator=lambda: next(lease_checks),
        )

        result = gateway.handle(ToolRequest(
            provider_tool_name="tga_workspace_write",
            arguments={"relative_path": "late.txt", "content": "late"},
            model_intent=ModelToolIntent(rationale="test lease fencing"),
            action_context=_context(),
            tool_call_id="call_lease_lost",
        ))

        assert result.error and result.error.code == "RUNNER_LEASE_LOST"
        assert len(adapter.calls) == 1
        persisted = bundle.tool_governance.find_by_tool_call(
            task.id, "solver_main", "call_lease_lost"
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
        solver = SingleSolverProvisioner(bundle).ensure(
            task=task, solver_id="solver_main"
        )
        definition = SolverDefinitionRegistry.builtin().require(solver.definition_id)
        intent = bundle.plans.get_global_plan(task.id).intents[0]
        manifest = ToolManifestBuilder().build(
            task=task,
            solver=solver,
            definition=definition,
            intent=intent,
            catalog=_catalog(task),
        )
        assert "retrieval_search" in manifest.provider_names
        adapter = _LegacyAdapter()
        gateway = ToolGovernanceGateway(
            task=task,
            manifest=manifest,
            repository=bundle.tool_governance,
            legacy_adapter=adapter,
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
            arguments={"query": "bounded reference", "channels": ["reference"]},
            model_intent=ModelToolIntent(rationale="find relevant reference material"),
            action_context=_context(),
            tool_call_id="call_retrieval",
        ))

        assert result.status == "succeeded"
        assert result.model_payload["retrieval_run_id"] == "run_audit"
        assert adapter.calls == []
        persisted = bundle.tool_governance.find_by_tool_call(
            task.id, "solver_main", "call_retrieval"
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
        solver = SingleSolverProvisioner(bundle).ensure(task=task, solver_id="solver_main")
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
            legacy_adapter=_ExplodingLegacyAdapter(),
        )

        result = gateway.handle(ToolRequest(
            provider_tool_name="tga_workspace_write",
            arguments={"relative_path": "report.txt", "content": "result"},
            model_intent=ModelToolIntent(rationale="persist the report"),
            action_context=_context(),
            tool_call_id="call_explodes",
        ))

        assert result.status == "failed"
        assert result.error and result.error.code == "EXECUTION_ADAPTER_ERROR"
        persisted = bundle.tool_governance.find_by_tool_call(
            task.id, "solver_main", "call_explodes"
        )
        assert persisted["status"] == "failed"
        assert persisted["result"]["error"]["code"] == "EXECUTION_ADAPTER_ERROR"
    finally:
        bundle.close()
