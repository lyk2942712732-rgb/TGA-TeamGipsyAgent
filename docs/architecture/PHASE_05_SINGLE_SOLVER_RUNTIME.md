# Phase 5 single-Solver runtime adapter

Schema-v6 tasks now execute through `SolverRunner`, which loads one durable
`SolverInstance` and delegates model turns to the compatibility execution
engine. The durable Solver has its own definition, model, tool-policy and Skill
snapshots, GlobalPlan assignment, LocalPlan and transcript. Its lifecycle is
persisted independently from the legacy runtime projection.

`ContextBuilder` is the only model-turn context selector. It builds a bounded
`ContextEnvelope` from the authoritative TaskSpec, active unreviewed hints,
Task Common and Solver Specialized Skills, the current plan, verified
task-scoped Knowledge, current-Solver candidate Knowledge and a protocol-valid
recent transcript. Retrieval remains an explicitly empty section until the RAG
phase. Legacy `StrategyCard` and `MemoryEntry` rows are not v6 authority and are
not scanned on each model turn.

User input is typed before it reaches context. The initial prompt becomes a
TaskDirective. The legacy `/hints` endpoint creates a UserIntervention and an
unreviewed TaskHint, never Knowledge or a StrategyCard. Instruction and
constraint interventions patch the TaskSpec without expanding
ExecutionPolicy.

Successful tool outcomes create Solver-scoped candidate Knowledge. Runtime
artifacts written through the compatibility store are schema-v6, append-only,
and visible through `EvidenceRepository`. Artifact creation alone never
confirms an EvidenceClaim. For CTF completion, the validator must first prove
that the submitted flag occurs in a task-owned immutable Artifact; only then is
a precise confirmed EvidenceClaim and evidence-backed verified task Knowledge
recorded.

Completion semantics are separated into `submit_solver_result` and
`propose_task_completion`. The model-visible `finish_session` name remains a
Phase-5 compatibility alias for the latter. Capability, artifact,
intervention, plan/knowledge, solver-result and task-completion handler
boundaries are present, while the existing governed execution pipeline remains
behind a compatibility adapter.

## Next boundary

Phase 5 does not introduce multiple Solvers, parallelism, RAG, new policy
defaults or a replacement execution pipeline. Phase 5A must establish the
ToolGovernanceGateway and related action-governance contracts before Phase 6
may add supervisor/worker orchestration.

The later Retrieval phase must preserve multi-scope ownership
(`global`, `workspace`, `task`, `solver`) for KnowledgeBase, Corpus, Document,
IndexSnapshot and RetrievalRun resources; `task_id` must not become a mandatory
owner for every retrieval object.
