# Domain glossary

- **TaskSpec**: authoritative objective, instructions, constraints, success criteria, and resources.
- **TaskHint**: user-supplied, unverified lead.
- **UserIntervention**: typed input received after Task creation.
- **SolverDefinition / SolverInstance / SolverRunner**: immutable template, durable Task-local identity, and temporary execution loop.
- **GlobalPlan / LocalPlan**: Supervisor-owned Intent DAG and Solver-owned plan.
- **KnowledgeItem**: scoped candidate, verified, rejected, or superseded knowledge.
- **Transcript**: one Solver's protocol messages and matched tool results; it is not Knowledge or the Event log.
- **Artifact**: immutable raw material or complete tool output.
- **EvidenceLocator**: precise text, line, JSON path, page, or binary coordinates. Offline migration may mark historical data as whole-artifact when the source stored no coordinates.
- **EvidenceClaim**: candidate, confirmed, or rejected statement linked to one Artifact and locator.
- **Finding**: candidate, confirmed, or rejected conclusion; confirmation requires a confirmed EvidenceClaim.
- **Skill**: reusable guidance that grants no tool authority.
- **ToolPolicy**: host-owned executable authority frozen for a SolverInstance.
- **OwnerScope**: global, workspace, task, or solver retrieval principal.
- **RuntimeSnapshot**: bounded schema-v6 Task projection, not a persistence dump.
- **EventEnvelope / EventBus**: durable sequenced record and process-local wake-up signal.
- **ApprovalQueue**: independently pending governed Actions; each decision applies to one Action.

Historical `MemoryEntry`, `StrategyCard`, and `SolverRecord` models exist only under
`tga.migrations`. Current Session lifecycle records live in `tga.domain.runtime`.
