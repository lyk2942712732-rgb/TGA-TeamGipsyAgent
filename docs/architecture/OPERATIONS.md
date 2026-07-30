# Operations

Install with `python -m pip install -e ".[dev]"`, build under `apps/web`, then
use `tga go` or `tga web`. Task authority is `runs/<task-id>/evidence.db`;
shared Artifact files and per-Solver workspaces live below the same task root.

Use the Task header to pause, resume, or cancel. Solver control and Approval
decisions remain separately scoped. Never edit SQLite rows or Transcript files
while a runner is active.

## Health

- Snapshot: `GET /api/v2/tasks/{id}/session`
- Event page: `GET /api/v2/tasks/{id}/events?after_seq=<n>`
- SSE: `GET /api/v2/tasks/{id}/events/stream?after_seq=<n>`

SQLite is authoritative; SSE only wakes clients. Reconnect from the last
durable sequence and request catch-up. Sequence gaps trigger Snapshot repair.
Local SQLite supports the bounded two-Worker envelope, not multiple independent
orchestrator processes writing the same Task database.

## Backup and recovery

Stop the Task before copying its directory. v5 migration defaults to dry-run.
`--apply` creates a database backup and Task JSON backup before atomic publish.
On a crash, preserve the directory and let recovery expire leases, merge
WorkerResults idempotently, and requeue recoverable Intent work.
