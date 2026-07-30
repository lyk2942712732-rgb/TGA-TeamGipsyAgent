# Phase 4 persistence schema v6

New tasks persist `TGATask.schema_version = 6`. The existing single-solver
runtime remains the execution adapter for this phase, while its durable task is
now accepted only at schema 6. Schema-v5 databases remain available through
`LegacyV5TaskReader`, which opens SQLite with `mode=ro` and `query_only=ON`.
Commands and report exports reject v5 tasks; Snapshot and event replay do not
open the writable `EvidenceStore`.

The normalized v6 tables live in
`tga/infrastructure/persistence/schema_v6.sql`. They retain the legacy tables
needed by the current adapter and add task specifications, hints,
interventions, global/local plans, intent dependencies, solver instances and
leases, knowledge conflicts/promotions, evidence claims, skill snapshots,
per-solver transcripts, approvals and their ownership indexes. `Database`
applies this schema additively and accepts task payloads at version 5 or 6; it
does not rewrite a task version during ordinary construction.

`PersistenceBundle` owns one SQLite connection and exposes implementations of
the Task, Solver, Plan, Knowledge, Evidence, Transcript and Event repository
ports. Its transaction context covers every adapter. Global plans use
versioned compare-and-swap, intent claim is one conditional update, leases are
keyed by `(task_id, solver_id)`, and schema-v6 artifacts use insert-only writes.

## Explicit migration

Migration is an offline operator action:

```text
python scripts/migrate_schema_v5_to_v6.py --db runs/<task-id>/evidence.db
```

The command validates a single schema-v5 task, creates a uniquely named
byte-for-byte backup, works on a temporary database, validates SQLite
integrity, and atomically replaces the source only after success. It creates a
conservative legacy `TaskSpec` without inferring formal directives or verified
evidence. Re-running it on schema 6 is a no-op and creates no second backup.

Legacy projections keep StrategyCards as draft legacy plans, hints as
unreviewed hints, evidence memory as candidate knowledge, and old findings as
candidate findings with a `legacy_whole_artifact` locator. No projection
promotes imported content to verified or confirmed state.

