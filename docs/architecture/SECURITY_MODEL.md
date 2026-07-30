# Security model

Model output, Hint text, Skill bodies, RAG documents, MCP responses, target
content and file names are untrusted. Authority comes only from host-owned
TaskSpec, ExecutionPolicy, Solver ToolPolicy, ActionContext and Approval state.

- Hint, Skill and RAG content cannot enlarge authorization.
- Worker ToolPolicy is isolated by role and Intent.
- Reviewer/Reporter lack active attack permissions; Reporter cannot confirm Finding.
- Task/Solver/Intent/Resource ownership is host-injected and checked.
- Artifact paths reject traversal; prompt injection is stored as a safety flag.
- High-impact Actions require one-time Approval, idempotency and resource lock.

API keys are write-only. Snapshot, Event, Action and MCP projections recursively
redact secret-like values and URL queries; logs never expose hidden model
reasoning. Remote MCP has no workspace mount. Docker MCP receives read-only
inputs and a dedicated Artifact write path under host-controlled limits.
