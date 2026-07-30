# Domain glossary

- **TaskSpec**: authoritative objective, instructions, constraints, success
  criteria and resources. It is distinct from unverified hints.
- **TaskHint**: user-supplied, unverified lead.
- **UserIntervention**: a typed hint, instruction, constraint, priority change,
  answer or approval received after task creation.
- **SolverDefinition / SolverInstance / SessionRunner**: reusable solver
  template, durable task-local identity and temporary execution loop. A
  Definition is immutable configuration; the same version may create Instances
  in many tasks. At most one Runner may execute a SolverInstance at a time.
- **GlobalPlan / LocalPlan**: supervisor-owned intent DAG and solver-private plan.
- **KnowledgeItem**: candidate, verified, rejected or superseded knowledge with
  solver, intent or task scope. Its kinds are fact, constraint, decision,
  failure boundary and hypothesis; `hint` and `evidence` are deliberately not
  knowledge kinds. A verified fact must cite an EvidenceClaim or explicit human
  source.
- **Transcript**: one solver's protocol messages and matched tool results; it is
  neither shared knowledge nor the event log.
- **Artifact**: immutable raw material or complete tool output.
- **EvidenceLocator**: a text/line range, JSON path, page, binary offset or an
  explicit legacy whole-artifact fallback.
- **EvidenceClaim**: a candidate, confirmed or rejected statement linked to one
  artifact and one precise locator.
- **Finding**: a candidate, confirmed or rejected conclusion. Confirmation
  requires at least one confirmed EvidenceClaim.
- **Skill**: reusable method or checklist. A skill grants no tool authority.
- **Task Common Skill / Solver Specialized Skill**: frozen task-wide guidance
  and frozen role/intent-specific guidance. New selections are normally limited
  to 1–2 common and at most 3 specialized Skills. Required capabilities are
  compatibility prerequisites, never permissions.
- **Tool**: executable capability governed independently from model text.
- **OwnerScope**: a retrieval principal: global, workspace, task, or solver.
  Task ownership is one valid scope, not a mandatory parent for retrieval data.
- **KnowledgeBase / CorpusSource**: a logical collection and one governed input
  source. A source declares owner, channel, kind, trust level, and metadata.
- **CorpusDocument / DocumentRevision / DocumentChunk**: stable document
  identity, append-only extracted version, and immutable locator-bearing text
  unit.
- **IndexSnapshot / IndexBinding**: a frozen multi-KnowledgeBase selection and
  the explicit principal/purpose pointer that pins recovery to that selection.
- **RetrievalRun / RetrievalHit / RetrievedContextPack**: an auditable query,
  ranked source matches, and a bounded labelled projection. They are reference
  material, never a Task fact merely because retrieval returned them.
- **RetrievalPolicy**: host-owned visibility, trust, source, channel, result,
  and context-budget constraints. Retrieved text cannot widen this policy.
- **RuntimeSnapshot**: a bounded task-level read projection. It summarizes the
  Team and durable domain state; it is neither an aggregate persistence dump
  nor a single current-Solver record.
- **EventEnvelope / EventBus**: the durable, monotonically sequenced event
  record and the process-local subscriber wake-up mechanism. SQLite remains
  authoritative; EventBus delivery alone never establishes domain state.
- **ApprovalQueue**: the set of independently pending governed Actions. A
  decision is scoped to one Action/Solver and does not imply that every other
  pending approval is resolved.

Legacy `TGATask`, `MemoryEntry`, `StrategyCard`, `ArtifactRecord`, `Finding`,
`SessionRecord` and `SolverRecord` names remain unchanged during compatibility
migration.

These legacy names are not schema-v6 write authorities. Their exact retained
uses and removal conditions are listed in `LEGACY_CLEANUP_REPORT.md`.

`TaskHint.status="verified"` records that a hint was reviewed; it does not
silently create verified Knowledge. Likewise, retrieval output is only source
material until a separate review/promotion operation accepts a claim.
