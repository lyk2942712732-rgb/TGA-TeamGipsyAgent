# Phase 5A tool governance stabilization

Phase 5A makes `ToolGovernanceGateway` the sole schema-v6 entry point for
model-initiated tool calls. The existing HTTP, Shell, Python, Workspace,
Input, and MCP implementations remain behind `LegacyToolPipelineAdapter`.
Schema-v5 dispatch and replay retain their compatibility path.

## New tool contract

The provider may supply only `ModelToolIntent`: rationale, expected outcome,
retry reason, alternative analysis, and a proposed effect. It cannot supply
Task, Solver, Intent, plan, policy-snapshot, or Strategy identifiers.

The host constructs an immutable `ActionContext` from durable runtime state:

```text
task_id + solver_id + intent_id + local_plan_step_id
+ orchestration_role + solver_definition_id
+ execution_policy_snapshot_id + solver_tool_policy_snapshot_id
+ skill_snapshot_id + attempt
```

`ToolRequest` combines provider name, validated arguments, model intent, host
context, and provider call ID. The gateway normalizes it into a durable
`GovernedAction`. Executors return `RawExecutionResult`; the gateway publishes
a bounded `ToolObservation` and `ToolGatewayResult`. Raw execution may create
Artifacts and candidates, but never represents a confirmed Finding.

The four business classes are:

| Class | Purpose | Router |
| --- | --- | --- |
| `control` | Solver/orchestrator state proposals | `ControlToolRouter` |
| `resource_read` | bounded reads of task-owned resources | `ResourceReadToolRouter` |
| `execution` | effectful capability and MCP execution | `ExecutionToolRouter` |
| `retrieval` | Phase-8 retrieval contract reservation | `RetrievalToolRouter` |

The retrieval router intentionally has no backend in this phase. Phase 8 must
retain `global`, `workspace`, `task`, and `solver` ownership scopes; Corpus,
Document, IndexSnapshot, and RetrievalRun must not require `task_id` as their
universal owner.

## Solver tool manifest example

`ToolManifestBuilder` intersects Task policy, Solver policy, definition tool
groups, orchestration role, specialty, current Intent, and the available
runtime catalog. `ToolDefinitionBuilder` emits provider schemas only from that
result.

```text
Supervisor: update_global_plan, propose_task_completion, confirm_finding,
            bounded shared reads
Worker:     update_local_plan, propose_knowledge, submit_worker_result,
            assigned execution and resource reads
Reviewer:   knowledge.inspect, artifact.inspect, review_evidence,
            review_finding, request_more_evidence
Reporter:   confirmed_knowledge.read, confirmed_evidence.read,
            confirmed_findings.read, report.write
```

Workers, reviewers, and reporters never receive Task-completion authority.
Reporters do not receive active execution. The Phase-5 single-Solver adapter
uses an explicit `phase5-single-solver-compatibility` policy profile; this does
not grant execution to ordinary supervisors.

## Action state machine

```text
proposed -> validated -> queued -> running -> succeeded|failed|blocked|cancelled
    |           |
    +-> denied  +-> pending_approval -> approved -> queued
                                   \-> rejected|expired|cancelled
```

Every transition is compare-and-swap persisted with its expected prior state.
Terminal states cannot re-enter execution. Results are immutable and repeated
identical completion writes are idempotent. Semantic-repeat detection,
idempotency reservation, and resource locking are independent mechanisms:

- semantic repeat asks for a new reason when the Solver repeats an analysis;
- idempotency prevents duplicate high-impact effects and supports recovery;
- resource locks serialize conflicting targets without serving as a ledger.

Task, Solver, and Intent budget checks use persistent reservations before I/O.
TaskBudget is the hard parent bound. A process-local semaphore is only a fast
throttle and is not authoritative accounting.

## Legacy adapter mapping

| New boundary | Legacy mapping retained in Phase 5A |
| --- | --- |
| `GovernedAction.context` | host-only `_host_action_context` metadata |
| `GovernedAction` identity | `ActionSpec.governed_action_id` |
| normalized execution arguments | existing dispatcher/handler arguments |
| effect and rationale | legacy `_tga` compatibility metadata |
| capability/resource read | existing capability and input handlers |
| MCP execution | frozen MCP route plus existing MCP transport |
| approved continuation | existing persisted `ActionSpec` executed once |
| `ActionResult` | `RawExecutionResult`, then bounded observation |

No transport or executor was rewritten. For schema v6, StrategyCard and
StrategyStep are no longer action authority and finding post-processing always
stores tool findings as candidates. Schema-v5 keeps its historical Strategy
and replay behavior.

## Legacy handler responsibilities not yet migrated

The adapter still relies on legacy handlers for concrete argument validation,
input materialization, HTTP/session behavior, workspace execution, Shell and
Python execution, MCP invocation, Artifact file registration/indexing,
observer hooks, and compatibility events. These are mechanical execution and
publication duties only; their Strategy updates are disabled for schema v6,
and they cannot directly mutate the v6 GlobalPlan through the gateway.

## Gateway interface available to Phase 6

Phase-6 Solver runners can depend on these stable operations without importing
legacy handler internals:

```python
gateway.handle(request: ToolRequest) -> ToolGatewayResult
gateway.resume_approved(legacy_action) -> ToolGatewayResult
gateway.resolve_without_execution(
    legacy_action, *, status: str, payload: dict
) -> ToolGatewayResult
```

`GatewayToolDispatcher` is the provider-call bridge: it parses
`ModelToolIntent`, injects the current host `ActionContext`, and calls the
gateway. Approval coordination affects only the owning Solver and Intent; the
Task Session remains running by default.

## Compatibility and migration status

- schema v6: all model tool calls use the manifest and governance gateway;
- schema v5: legacy dispatcher and replay remain readable and executable;
- completion: `propose_task_completion` is distinct from
  `submit_worker_result`; the hidden `finish_session` alias exists only to
  replay an already persisted Phase-5 supervisor transcript;
- evidence: Artifact existence never auto-confirms Evidence or Finding;
- next phase: orchestration may call the gateway contract, but must not depend
  on legacy handler internals.
