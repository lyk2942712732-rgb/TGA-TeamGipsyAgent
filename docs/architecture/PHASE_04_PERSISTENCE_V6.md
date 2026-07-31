# Phase 4 persistence schema v6

> Historical phase record, updated to describe the final Cutover state.

New and existing current Tasks persist `TGATask.schema_version = 6`. Database initialization,
repositories, application services, API, and Runtime reject every other Task schema. Current
schema initialization does not create old Solver, Memory, Strategy, Action, Event, or result
tables.

Historical schema-v5 databases are opened only by `tga.migrations.legacy_v5` during an
explicit offline migration. The reader uses SQLite `mode=ro` and `query_only=ON`. Migration
retains a byte-for-byte backup, original Task JSON, object mapping report, and historical
Runtime archive; creates current objects on a copy; verifies integrity; drops old Runtime
tables; and only then publishes schema v6.

No ordinary constructor, Snapshot, Event, report, or command path opens schema v5.
