# Target architecture

TGA is migrating from a persistent single-Agent ReAct runtime to one task-level
orchestrator coordinating durable solver identities. The intended dependency
flow is:

```text
apps -> application -> runtime -> domain
                         ^
                         |
              infrastructure implements application ports
```

The target packages are `tga/domain` (task, solver, planning, knowledge,
evidence, skills, retrieval and governance concepts), `tga/application`
(commands, queries, services and ports), `tga/runtime` (orchestration, agents,
context, tooling, scheduling, retrieval, completion and events), and
`tga/infrastructure` (persistence, LLM, MCP, skills, workspace and event
adapters).

One `TaskOrchestrator` owns a task. A supervisor owns the shared `GlobalPlan`;
workers own local plans and may propose intents, but cannot complete the task or
mutate the global plan directly. Every solver has its own transcript and at
most one active runner. Artifacts are immutable raw material; evidence claims
locate support within artifacts; verified findings are conclusions. Retrieval
results and user hints remain candidates until reviewed.

Phase 4 introduces schema v6 and repository adapters while preserving the
single-solver execution adapter, public API, event stream and frontend. Schema
v5 is replay-only unless an operator completes the explicit v5-to-v6 migration.

Phase 5A establishes the runtime tool boundary used by later orchestration.
Provider calls are reduced to non-authoritative intent, the host injects Task /
Solver / Intent ownership, role-scoped manifests define visibility, and one
gateway owns authorization, approval, lifecycle, persistent budget,
idempotency, resource locks, routing, and bounded observations. Concrete legacy
executors remain wrapped behind an adapter until their later migration phases.

Phase 6 activates the task-level `TaskOrchestrator` with a durable Supervisor,
serial dependency-aware Worker dispatch, explicit assignments, recoverable
structured results, and minimal Reviewer/Reporter roles. The existing Manager
surface is a compatibility facade whose run and lifecycle operations keep the
durable orchestrator synchronized. See `PHASE_06_SERIAL_ORCHESTRATION.md`.

Phase 7 permits at most two dependency-independent Workers. Task and Solver
schedulers use renewable fenced leases, atomic Intent claims, plan CAS,
per-Solver workspaces, immutable Artifact publication, scoped approvals, and
hierarchical durable budgets. SQLite remains the only coordination store and
is deliberately limited to this two-Worker envelope. See
`PHASE_07_LIMITED_PARALLELISM.md`.

Phase 8 establishes retrieval as an independent domain. Knowledge Bases,
sources, documents and revisions, chunks, frozen snapshots, bindings, runs,
and hits support global, workspace, task, and solver ownership; `task_id` is
not a universal foreign key. Governed keyword retrieval separates Skill,
Reference, and Task Artifact channels, applies explicit trust and visibility
policy, and injects only bounded, labelled, untrusted context. Artifact hits
remain candidate evidence. See `PHASE_08_RETRIEVAL_DOMAIN.md`.

Phase 9 moves the public boundary to application Commands and Queries. The
schema-v6 RuntimeSnapshot is a bounded task projection with explicit Team,
Supervisor, Solver, Intent, approval-queue, retrieval, evidence, and event
summaries. Canonical event envelopes carry both Solver and Intent identity;
SQLite is authoritative and an in-process EventBus only wakes subscribers.
Schema-v5 remains a separate read-only projection. See
`PHASE_09_API_EVENTS_PROJECTIONS.md`.

Phase 10 makes the browser boundary task-centric. Wire snapshots are adapted
once into normalized entity maps; components consume selectors rather than
array positions or transcript-derived state. Incremental event reduction is
idempotent, version/sequence aware, and repaired from the authoritative API on
gaps. Schema-v5 is a read-only adapter, not a condition distributed across the
component tree. See `PHASE_10_FRONTEND_DATA_FOUNDATION.md`.

Phase 11 realizes the browser command workbench over that boundary. Task-level
progress and governance remain separate from per-Solver inspection; Intent,
timeline, evidence, resource, and mode-scene views read durable projections.
The UI sends only application commands for control, Approval decisions, and
scoped intervention. Replay reduces canonical events into the same normalized
store and is read-only, including schema-v5 compatibility. See
`PHASE_11_MULTI_SOLVER_WORKBENCH.md`.

Phase 12 makes schema v6 the release default and confines old semantics to
named compatibility, migration, or concrete execution-adapter boundaries. New
Tasks no longer write the legacy Task-level Skill bundle, v6 execution no
longer updates Memory/StrategyCard, and release operations document backup,
recovery, security, Retrieval trust, frontend replay, SQLite limits and
measured performance. See `LEGACY_CLEANUP_REPORT.md`.
