# Phase 6: serial Supervisor-Worker orchestration

Phase 6 replaces task-level single-agent ownership with a durable
`TaskOrchestrator`. The release-window `Manager` API remains available as a
facade, but solver provisioning, runner dispatch, and lifecycle synchronization
are delegated to the orchestrator.

## Runtime shape

One task owns one persisted `TeamRuntimeState` and one Supervisor. The
Supervisor owns the `GlobalPlan`, creates Intents, requests specialist roles,
and is the only role allowed to propose task completion. Workers, Reviewers,
and Reporters are created lazily from the mode-specific `TeamTemplate`.

The first implementation is intentionally serial:

- `max_active_workers` is fixed at one;
- `IntentDispatcher` selects only dependency-ready Intents;
- `SolverSelector` deterministically selects an allowed specialist definition;
- each Worker has a private transcript, `LocalPlan`, workspace, immutable skill
  snapshot, tool-policy snapshot, and budget;
- `ResultMerger` validates task ownership before applying a `WorkerResult`;
- Reviewer and Reporter attempts use stable role/attempt identities and may be
  retried without reusing a failed solver.

Five task modes have a bootstrap route: CTF, penetration test, incident
response, vulnerability research, and reverse engineering.

## Authority and tool boundary

Every model-originated solver tool call still crosses the Phase 5A
`ToolGovernanceGateway`. Role manifests and host-owned `ActionContext` enforce
the following boundaries:

- Supervisor: GlobalPlan and team control, review/report requests, completion
  proposal;
- Worker: LocalPlan proposals, candidate knowledge, and
  `submit_worker_result` only;
- Reviewer: candidate evidence/knowledge/finding reads and `ReviewResult`;
- Reporter: confirmed evidence/knowledge/finding reads and `ReportResult` only.

An assignment filters Worker input/context to `allowed_resources`; the task's
complete raw input set is not included. Candidate knowledge and findings are
not promoted by result merge. Only an explicit Reviewer result may confirm or
reject them. Reporter operations cannot mutate fact state or complete a task.

## Completion and lifecycle

Worker completion, task-completion proposal, and mode-specific completion
validation are separate operations. A successful `WorkerResult` completes its
Intent and Worker but leaves the task runtime running. Only a Supervisor
proposal is passed to the supplied completion validator.

Persisted state vocabularies are:

- TaskOrchestrator: `created`, `running`, `paused`, `awaiting_input`, `blocked`,
  `completed`, `failed`, `cancelled`;
- Solver: `created`, `queued`, `running`, `awaiting_approval`, `paused`,
  `completed`, `blocked`, `failed`, `cancelled`;
- Intent: `pending`, `assigned`, `running`, `reviewing`, `completed`, `blocked`,
  `failed`, `cancelled`; records produced by offline migration retain explicit
  provenance but do not activate an alternate Runtime state machine.

A blocked Worker does not directly block the task. The dispatcher may run a
different ready Intent; the task moves to `awaiting_input` only when no work is
runnable and unresolved blocked/failed Intents remain. Pause/resume preserves
the assignment. Cancellation atomically abandons the plan and cancels all
nonterminal Intents, assignments, and Solvers.

## Durability and recovery

Schema-v6 persistence now includes `solver_assignments`,
`worker_result_merges`, `review_results`, `report_results`, and
`task_orchestrator_states`. Stable solver/result IDs and immutable payloads make
replay idempotent. On restart, `recover()` merges a persisted but unmerged
Worker result exactly once; it does not recreate its Solver or rerun tools.
Template-content hashes fail closed if a task's team definition changes after
bootstrap.

Phase 6 tests cover the five modes, serial dispatch, role and resource
isolation, Gateway-only Worker submission, blocked-worker alternatives,
review/report authority, completion separation, solver limits, pause/resume,
cancellation, retry attempts, and crash recovery.
