# Legacy cleanup report

This Phase-12 scan classifies retained old names. Legacy code is allowed only
for schema-v5 replay, explicit migration, or a named concrete adapter; it is
not a second schema-v6 authority.

| Symbol or assumption | Classification | Boundary and disposition |
| --- | --- | --- |
| `MemoryEntry`, `MemoryKind evidence/hint` | v5 read-only + migration | Legacy models/repositories and conservative converters retain them. v6 uses `TaskHint` and candidate `KnowledgeItem`. |
| `StrategyCard`, `StrategyStep` | v5 read-only + migration | Legacy reader/report and concrete execution adapter retain them. v6 uses `GlobalPlan`, `Intent`, and `LocalPlan`; v6 strategy writes are disabled. |
| `active_solver_id` | deprecated compatibility | Remains in legacy Session/coordinator/report/frontend adapter. v6 uses `supervisor_solver_id` and stable Solver IDs. |
| global `turn_count` assumption | deprecated metric | Retained only as aggregate compatibility/budget data. Scheduling and Transcript state are per Solver. |
| `TGATask.skill_bundle_snapshot` | deprecated compatibility | Read when adapting an old caller/task. New v6 creation persists `TaskCommonSkillSnapshot` and `SolverSkillSnapshot`. |
| model-visible `finish_session` | deprecated alias | Hidden from Worker/Reviewer/Reporter. Supervisor routing maps it to governed completion validation; canonical v6 semantics are `propose_task_completion`. |
| `solvers[0]` | test-only assertion | Runtime/frontend select by ID/role. Remaining uses only assert one-item fixtures. |
| single `pendingApproval` | legacy frontend only | Old page remains behind `VITE_RUNTIME_PAGE=legacy`; current UI uses `ApprovalQueue`. |
| Evidence ≈ Artifact naming | deprecated compatibility | `EvidenceStore` and old report fields retain names. v6 separates Artifact, EvidenceClaim/locator, and Finding. |
| `Manager` | compatibility Facade | API/CLI lifecycle calls delegate to `TaskOrchestrator`. |
| `runtime/handlers.py` | deprecated concrete adapter | HTTP/workspace/MCP execution stays wrapped by `LegacyCapabilityHandlerAdapter`; planning, knowledge, completion, result, Approval and governance handlers are split out. |
| `runtime/context.py` | deprecated runner context | v6 authority is `runtime/context/context_builder.py`; the old module supports concrete protocol execution. |
| `tga/contracts.py` | compatibility export | Identity-preserving exports only; canonical models live under `tga/domain`. |

## Phase-12 changes

- Replaced stale EvidenceMemory/StrategyCard browser E2E with five TaskMode
  command-workbench fixtures.
- New v6 Task creation no longer writes Task-level specialized Skill state.
- v6 Observer and capability execution no longer write legacy Memory/Strategy.
- Empty historical `tga/skills/builtin/*` content is superseded by
  `resources/skills`; no loader targets the old tree.

## Double-write audit

Schema-v6 authority is stored only in TaskSpec/Hint/Intervention, Plan/Intent,
Knowledge, Evidence, Solver, Transcript, Approval, Retrieval, and Event tables.
Legacy Memory/Strategy may be read for compatibility but v6 control flow does
not update them. Retain v5 read-only replay until a published compatibility
deadline.
