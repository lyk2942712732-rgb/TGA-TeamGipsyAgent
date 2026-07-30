# TGA governed AgentSession safety model

> Historical single-Agent notes retained for compatibility archaeology. The
> authoritative schema-v6 document is
> [architecture/SECURITY_MODEL.md](architecture/SECURITY_MODEL.md).

## Input trust boundary

Schema-v5 Session input is one initial user prompt plus staged task files. The
backend does not trust client paths, MIME declarations,
stored names, sizes, or digests. It generates asset IDs/stored names, rejects
unsafe filenames, streams bytes through size limits, detects MIME from content
where supported, and records SHA-256 metadata.

Each Session owns one persistent workspace:

```text
workspace/inputs/files
workspace/artifacts
workspace/evidence
workspace/tool-results
workspace/state
```

Original inputs are immutable and checksum-verified before Agent reads. Derived
or modified content must be written to `artifacts`. A failed creation removes the
partial Session tree while retaining traceable staging for bounded retry; an
expiry sweeper removes unclaimed staging.

Initial prompt text and attachments are untrusted context. They create only the
creation-time `task_sources` URL seeds; later hints, artifacts, and model/OCR
output never widen network authorization, filesystem roots, process permission,
high-impact authority, or MCP permission.

## Execution boundaries

Removing task-level MCP selection does not remove execution governance. The
enforced boundaries remain:

- network access, interaction, seed/custom origins, rate, and concurrency;
- fixed read-only inputs plus writable work/artifacts workspace layout;
- isolated local compute mode, timeout, and concurrency;
- high-impact forbidden/approval-required/allowlist policy;
- per-server MCP rate, concurrency, timeout, output, and transport controls.

The legacy Manager created a candidate StrategyCard before Agent execution and bound
actions to a strategy step, rationale, and expected outcome. The controlled
executor validates capability input and scope; AgentSession cannot widen it.

## MCP authority

The operator-owned global MCP registry is the sole MCP management source. A new
Session records a creation-time service/tool catalog snapshot for audit and
visibility, not as a user-selected grant. New services affect only subsequently
created Sessions. Global disable/removal is checked again before every call and
immediately blocks existing Sessions.

Active MCP methods require a relevant general execution boundary. High-impact
methods are forbidden, require durable user approval, or require an exact
`mcp:<server>.<method>` allowlist entry. Host Windows paths are rejected for
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

HTTP state uses one in-memory CookieJar per task, Agent session, and origin. Cookie and
authorization values are excluded from events, reports, checkpoints, and UI.
Raw tool output remains an immutable Artifact. Derived indexes and excerpts are
non-authoritative projections with stable source references.

Candidate findings and flags cannot complete a task. Completion requires
task-owned evidence through the shared CompletionGate. Observer receives bounded,
redacted state and may return only an ObserverPatch; it cannot call tools or mark
a task solved.

The full audit transcript is retained while the provider receives a bounded
working context that preserves assistant/tool protocol pairs and schema-v5 input
metadata. `GET` endpoints are read-only; material report export is an explicit,
audited `POST` operation.

## Historical schema boundary

Schema 2/3 rows and user files are preserved for explicit offline migration,
but the live Runtime API and Manager reject them with
`SCHEMA_VERSION_UNSUPPORTED`. The product does not project old URL/reference,
MCP Resource/Tool, or task-level MCP ACL semantics into the current UI and does
not execute or dual-write them. Only schema-v5 Session inputs and the creation
time MCP catalog snapshot participate in authorization.
