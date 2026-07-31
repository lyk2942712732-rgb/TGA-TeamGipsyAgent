# Phase 9 API, events, snapshots, and projections

FastAPI routes validate transport DTOs and delegate to application Commands and Queries.
Route modules do not open SQLite directly or construct the persistence store.

The schema-v6 Runtime Snapshot contains bounded Task, Session, Team, Solver, Intent, plan,
Knowledge, Evidence, governed Action, Approval, Retrieval, and recent Event projections.
Collections are bounded; events and evidence provide paginated endpoints. SSE catches up from
the authoritative event table, then uses an in-process bus only as a wake-up signal.

Task control, typed interventions, Approval decisions, Solver control, and Intent retry verify
ownership before mutation. Event payloads are size/depth bounded and carry canonical Task,
Solver, Intent, sequence, type, payload, and timestamp identity.

Schema v5 is rejected by every API read and command with `SCHEMA_VERSION_UNSUPPORTED` and a
Migration Required message. It can only be read by the offline migration tool; no API request
silently upgrades or falls back to a historical projection.
