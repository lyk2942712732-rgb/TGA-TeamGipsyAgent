# Migration plan

| Current | Migration destination |
| --- | --- |
| `tga/contracts.py` | domain task, governance, evidence and solver models; compatibility re-export remains |
| `EvidenceStore` and repository mixins | infrastructure persistence adapters implementing application ports |
| `AgentSessionRunner` | `SolverRunner` for one durable Solver, with the old class retained as its execution adapter |
| `Manager` / `SessionCoordinator` | task orchestration services, initially through adapters |
| task transcript | per-solver transcript repository |
| `MemoryEntry` | explicit knowledge domain with scope and verification state |
| `StrategyCard` | global/local planning concepts |
| capabilities and MCP tools | governed execution adapters behind a tool gateway |
| `tga/rag` | independent retrieval domain and infrastructure |
| API snapshot helpers | application queries/read models |
| single-Agent Runtime page | task command workbench with team and intent views |

Execution proceeds in the numbered prompt order. Schema v5 data stays readable;
schema v6 is introduced only in its dedicated migration phase. Each phase keeps
compatibility adapters until its cleanup phase explicitly removes them.
Phase 5A remains mandatory before multi-Solver orchestration can depend on the
runtime tool boundary. Phase 5A is implemented: schema-v6 calls now use
`ToolGovernanceGateway`, role-scoped manifests, host-owned action context,
durable lifecycle/idempotency/lock/budget records, and the legacy execution
adapter. See `PHASE_05A_TOOL_GOVERNANCE.md`.

Phase 6 is implemented: `TaskOrchestrator` now owns durable team state, serial
Supervisor-Worker dispatch, result merge, Reviewer/Reporter attempts,
completion proposals, recovery, and Manager lifecycle delegation. The
single-solver runner remains only as the compatibility execution adapter. See
`PHASE_06_SERIAL_ORCHESTRATION.md`.

Phase 7 is implemented: Tasks may opt into two independent Workers while
serial mode remains supported. Fenced Task/Solver leases, atomic claims,
GlobalPlan CAS, immutable per-Solver workspace publication, scoped approval,
hierarchical usage ledgers, network permits, and bounded SQLite lock handling
form the concurrency boundary. See `PHASE_07_LIMITED_PARALLELISM.md`.

Phase 8 retrieval work must use the Phase 7 scheduling and ownership seams
without making `task_id` mandatory for Corpus, Document, IndexSnapshot, or
RetrievalRun. Global, workspace, task, and solver ownership must remain valid,
including multi-KnowledgeBase retrieval and the existing corpus/document
revision and trust concepts.

Phase 8 is implemented: the independent retrieval domain and SQLite
repositories support all four owner scopes, append-only document revisions,
frozen multi-KnowledgeBase snapshots, persistent context bindings, auditable
runs/hits, keyword-first retrieval with optional vector fallback, and governed
ContextBuilder/tool integration. Task Artifact projection is derived and
best-effort; retrieval-only provenance cannot become verified Knowledge. See
`PHASE_08_RETRIEVAL_DOMAIN.md`.

Phase 9 is implemented: the API now delegates to application Commands and
Queries, exposes bounded task/team/Solver/Intent projections, treats approvals
as a scoped queue, and publishes versioned canonical events. Event and timeline
streams combine authoritative cursor-based database catch-up with a
non-authoritative process EventBus. Schema-v5 snapshot/replay remains
read-only, while v6 control endpoints reject v5 explicitly. The OpenAPI
contract for Phase 10 is snapshot-tested. See
`PHASE_09_API_EVENTS_PROJECTIONS.md`.

Phase 10 is implemented: the frontend parses v6 and read-only v5 snapshots at
one boundary, stores runtime entities by stable ID, derives team/intent/
approval/evidence views through selectors, and reduces multi-entity events
with sequence-gap recovery. The task-level page skeleton supports stable URL
selection and accessible responsive panels. The old single-Agent Runtime page
remains available through `VITE_RUNTIME_PAGE=legacy`. See
`PHASE_10_FRONTEND_DATA_FOUNDATION.md`.

Phase 11 is implemented: the default task runtime is a multi-Solver command
workbench with aggregate Task controls, an instantiated Team tree, durable
Intent work items, filtered canonical activity, provenance-preserving evidence
and resources, per-Solver inspection, queued one-time Approvals, scoped
interventions, five mode scenes, and sequence replay. Large projections are
bounded or paged, sequential SSE events are batched, and v5 remains read-only.
See `PHASE_11_MULTI_SOLVER_WORKBENCH.md`.
