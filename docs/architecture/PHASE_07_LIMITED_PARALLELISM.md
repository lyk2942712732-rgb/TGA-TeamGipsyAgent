# Phase 7: limited parallelism and concurrency safety

Phase 7 raises the schema-v6 runtime from serial dispatch to at most two
independent Workers per Task. Serial execution remains the default unless a
Task explicitly sets `execution_budget.max_active_workers` to `2`.

## Scheduling and leases

`TaskScheduler` schedules one `task_id` under a fenced
`TaskOrchestratorLease`. `SolverScheduler` schedules one `(task_id, solver_id)`
under a fenced `SolverLease`. Both schedulers renew their leases from a
separate SQLite connection and cancel the run context when renewal fails.

Every lease records its owner, fencing token, expiry, and last renewal. A
different owner can recover work only after expiry; the fencing token then
increments. Release is idempotent, and a stale owner cannot renew, release, or
submit a Worker result. `ToolGovernanceGateway` checks the active Solver lease
before execution and again before accepting the raw result.

`ConcurrencyLimiter` supplies a fast process-local limit while the durable
lease tables remain authoritative. SQLite also counts active Worker leases so
separate processes cannot exceed the Task's configured maximum.

## Plans, intents, and knowledge

Dependency readiness and Intent claim happen in one short immediate
transaction. A claim updates the persisted Intent lifecycle atomically, so two
runners cannot both win. A failed retry resets the Intent and creates a new
Solver assignment with an incremented attempt. Governed high-impact Action
idempotency includes the attempt, preventing restart duplication without
collapsing an explicit new attempt into the old result.

The Supervisor is the only GlobalPlan writer. Updates use version CAS and
bounded reload-and-merge. Plan replacement preserves concurrently claimed
Intent lifecycle fields, and each successful mutation emits `PLAN_UPDATED`
with `old_version` and `new_version`. Workers may only submit proposals. Local
plans remain private to their Solver and all model-initiated access is fenced
by that Solver's runner lease.

Candidate Knowledge stays Solver/Intent scoped. `KnowledgeConflictDetector`
compares explicit structured `subject` and `value` fields.
`KnowledgePromotionService` queues conflicting Task-scope promotions for
review; it never overwrites the earlier candidate. Rejected and superseded
items remain durable audit records.

## Workspace and governed I/O

The schema-v6 layout is:

```text
workspace/
  inputs/                    # shared, read-only to Workers
  shared/artifacts/          # immutable, append-only publication
  solvers/<solver-id>/
    scratch/
    outputs/
```

Solver writes are path-confined to their own `scratch` and `outputs`
directories. `SolverWorkspaceService.publish_artifact` publishes a
content-addressed immutable file using an atomic hard-link operation. Optional
`BudgetManager` integration reserves the Artifact byte count before publish;
identical content is counted once for a Task.

High-impact Actions use durable idempotency keys and exclusive resource locks.
HTTP Actions additionally acquire a durable Task-scoped network permit. The
permit enforces configured concurrent requests, per-minute request rate, and
total network request budgets before network I/O begins.

## Approval and budget scopes

Pending approvals are Action records and may coexist. Awaiting approval pauses
the owning Solver/Intent, not unrelated Workers or the Task. Task-level pause is
reserved for explicit pause/cancel, global authorization or safety changes,
Supervisor loss, dependency deadlock on approvals, or exhausted Task budget.

`TaskBudget` is the hard parent of `SolverBudget` and `IntentBudget`. Durable
usage covers turns, input/output/total tokens, tool calls, Artifact count and
bytes, total and active Solvers, and network count/rate/concurrency. Usage is
idempotent and aggregated across all Solvers before accepting a reservation.

## SQLite constraints

Each Worker uses an independent connection. Transactions exclude model,
network, and MCP calls and use bounded `BEGIN IMMEDIATE` lock retries. WAL mode,
a finite busy timeout, scheduling indexes, bounded event pagination, and
`db_write_lock_metrics` keep the local two-Worker operating envelope explicit.
No external database or distributed lock service is introduced.

## Stable seams for Phase 8

Phase 8 may depend on `TaskScheduler`, `SolverScheduler`, `ConcurrencyLimiter`,
`CancellationToken`, `BudgetManager`, `NetworkBudgetLimiter`, fenced lease
managers, atomic Intent claim, GlobalPlan CAS, Solver workspace publication,
and scoped Knowledge promotion.

Retrieval ownership must remain independent of this scheduling unit. Corpus,
Document, IndexSnapshot, and RetrievalRun may be global-, workspace-, task-,
or solver-scoped; `task_id` must not become a mandatory owner. Phase 8 must
preserve KnowledgeBase, CorpusSource, Document Revision, TrustLevel,
Owner/Scope, IndexSnapshot, and multi-KnowledgeBase retrieval.
