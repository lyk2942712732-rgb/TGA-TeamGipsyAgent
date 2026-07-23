# TGA governed AgentSession safety model

## Input trust boundary

Schema-v4 Session input is limited to staged task files, optional Hint text, and
Hint attachments. The backend does not trust client paths, MIME declarations,
stored names, sizes, or digests. It generates asset IDs/stored names, rejects
unsafe filenames, streams bytes through size limits, detects MIME from content
where supported, and records SHA-256 metadata.

Each Session owns one persistent workspace:

```text
workspace/inputs/task
workspace/inputs/hints
workspace/artifacts
workspace/evidence
workspace/tool-results
workspace/state
```

Original inputs are immutable and checksum-verified before Agent reads. Derived
or modified content must be written to `artifacts`. A failed creation removes the
partial Session tree while retaining traceable staging for bounded retry; an
expiry sweeper removes unclaimed staging.

Hint text and attachments are untrusted context. They never add network scope,
filesystem roots, process permission, state-change authority, or MCP permission.

## Execution boundaries

Removing task-level MCP selection does not remove execution governance. The
enforced boundaries remain:

- network mode, exact allowed scopes, rate, and concurrency;
- filesystem read-only/workspace-write mode and allowed roots;
- process execution mode and timeout;
- bounded fuzzing budgets;
- state-change and incident-containment modes/action allowlists;
- per-server MCP rate, concurrency, timeout, output, and transport controls.

Manager creates a candidate StrategyCard before Solver execution and binds
actions to a strategy step, rationale, and expected outcome. The controlled
executor validates capability input and scope; AgentSession cannot widen it.

## MCP authority

The operator-owned global MCP registry is the sole MCP management source. A new
Session records a creation-time service/tool catalog snapshot for audit and
visibility, not as a user-selected grant. New services affect only subsequently
created Sessions. Global disable/removal is checked again before every call and
immediately blocks existing Sessions.

Active MCP methods require a relevant general execution boundary. Destructive
methods additionally require `state_change.mode = authorized` and an exact
`mcp:<server>.<method>` allowed action. Host Windows paths are rejected for
Docker MCP calls.

Local Docker MCP calls receive the Session workspace as `/workspace:ro` and only
`/workspace/artifacts` as read-write. Discovery receives no task mount. Remote
HTTP/SSE MCP services receive no local mount and are explicitly reported as
remote.

## Model and evidence boundaries

`SessionContextBuilder` creates a deterministic, auditable initial context with
mode, Hint, file metadata/paths, MCP snapshot, workspace rules, execution
boundaries, and completion conditions. It does not inline arbitrary binary or
archive bytes. Vision-capable models receive bounded real image content blocks;
text-only models receive paths and explicit image-analysis guidance.

HTTP state uses one in-memory CookieJar per task, Solver, and origin. Cookie and
authorization values are excluded from events, reports, checkpoints, and UI.
Raw tool output remains an immutable Artifact. Derived indexes and excerpts are
non-authoritative projections with stable source references.

Candidate findings and flags cannot complete a task. Completion requires
task-owned evidence through the shared CompletionGate. Observer receives bounded,
redacted state and may return only an ObserverPatch; it cannot call tools or mark
a task solved.

The full audit transcript is retained while the provider receives a bounded
working context that preserves assistant/tool protocol pairs and schema-v4 input
metadata. `GET` endpoints are read-only; material report export is an explicit,
audited `POST` operation.

## Legacy compatibility

Schema 2/3 task payloads, URL/reference inputs, MCP Resource/Tool records, and
old MCP ACL fields remain readable for historical Sessions. Schema-aware readers
use their legacy `inputs`/`artifacts` roots. Those fields do not participate in
new schema-v4 Session creation or authorization.
