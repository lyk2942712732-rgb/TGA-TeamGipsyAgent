# Phase 9: API, events, snapshots, and projections

Phase 9 replaces the single-agent HTTP read model with task-level application
commands and queries. FastAPI routes validate transport DTOs and delegate to
`RuntimeCommands` or `RuntimeQueries`; route modules do not open SQLite or
construct `EvidenceStore`.

## Runtime snapshot

The schema-v6 `RuntimeSnapshotResponse` contains the Task, aggregate Session,
Team, every bounded Solver summary, Intents, WorkerResults, GlobalPlan,
Knowledge, Artifact/Evidence/Finding summaries, Actions, pending Approvals,
RetrievalRun summaries, and a recent event page with `latest_seq`.

The Session projection identifies the Supervisor explicitly and reports active
Solver count, worker limit, hierarchical budget usage, stop reason, and
timestamps. No array position represents the current Solver. Artifact paths,
content, and unbounded provenance are excluded. Snapshot collections and the
recent event window are bounded to at most 100 entries; bulk evidence,
timeline, and event history use paginated queries.

## Commands and ownership

Task control, typed interventions, approval decisions, Solver control, and
Intent retry are application commands. Every Solver/Intent target is checked
against the Task before mutation. The compatibility `/hints` endpoint creates
a task-scoped `UserIntervention(kind=hint)`, reports its deprecation and does
not expose a `memory_id` as its meaning.

Approvals are a queue rather than a single Session flag. Each projection
contains Solver, Intent, Action, risk/effect, reason, alternatives, and
deadline. A decision changes only its Action. A Solver remains
`awaiting_approval` while another pending approval still belongs to it.

## Events and streaming

New schema-v6 events use the envelope:

```text
schema_version, id, task_id, seq, type, solver_id, intent_id, payload, created_at
```

Payloads are normalized through `VersionedEventPayload`. They carry their own
payload schema version and enforce byte, depth, object-key, list-length, key,
and JSON-value bounds. Core orchestration, Solver, Intent, merge, knowledge,
evidence, approval, retrieval, and completion events additionally validate
their required identifiers.

SSE first catches up from the authoritative database using `after_seq`, then
waits on a bounded process-local `EventBus`. A notification always triggers a
database re-read; the bus is not durable storage. The 15-second authoritative
fallback repairs missed notifications and supplies heartbeats without a
per-browser one-second full-table poll. Subscriber disconnects only close the
stream and never affect Task execution.

`/events` and `/timeline` share the canonical cursor-paginated envelope.
Evidence uses offset pages whose limit/offset are applied by SQLite.

## Legacy compatibility

Schema-v5 Snapshot and event replay use `LegacyV5TaskReader` in SQLite
read-only/query-only mode and return `schema_version=5`. All v6 Solver and
Intent commands reject v5 explicitly. Earlier schema versions are rejected
with `SCHEMA_VERSION_UNSUPPORTED`; no API read silently upgrades or mutates
them.

The OpenAPI subset used by the runtime UI is frozen in
`tests/snapshots/phase9_openapi_contract.json`. Phase 10 should build its
frontend adapter against these DTO names and paths, keeping the schema-v5
adapter separate.
