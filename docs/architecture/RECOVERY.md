# Recovery

Recovery uses durable schema-v6 Task, Solver, Intent, Transcript, Action, Approval,
WorkerResult, lease, and Event records. Runners and EventBus subscribers are disposable.

1. Validate SQLite integrity and TaskOrchestrator state.
2. Expire stale Task/Solver lease ownership using fencing tokens.
3. Reconcile each Solver with its own Transcript and assignment.
4. Merge WorkerResults idempotently and advance only their Intent.
5. Restore pending Approval without executing its Action.
6. Resume runnable Intents within budgets and the two-Worker limit.
7. Reconnect SSE from the last durable sequence or rebuild from Snapshot.

Schema v5 has no Runtime recovery or replay path. Offline migration works on a copy and
retains rollback artifacts. Current schema-v6 Replay is read-only and does not replace
Transcript recovery.
