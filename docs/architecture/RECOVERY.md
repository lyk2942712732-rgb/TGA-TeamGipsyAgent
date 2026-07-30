# Recovery

Recovery uses durable Task, Solver, Intent, Transcript, Action, Approval,
WorkerResult, lease and Event records. Runners and EventBus subscribers are
disposable.

1. Validate SQLite integrity and read TaskOrchestrator state.
2. Expire stale Task/Solver lease ownership using fencing tokens.
3. Reconcile each Solver with its own Transcript and assignment; never merge
   Transcript streams.
4. Merge persisted WorkerResults idempotently and advance only their Intent.
5. Restore pending Approval without executing its Action.
6. Resume runnable Intents within budgets and the two-Worker limit.
7. Reconnect SSE from the last durable sequence or rebuild from Snapshot.

GlobalPlan uses compare-and-swap and Intent claim is atomic. A stale runner
cannot publish after losing its lease. Shared Artifact publication is
append-only; Solver workspaces are separate.

Schema v5 replay performs no recovery writes. Explicit migration works on a
temporary copy and can rollback from backup after publication failure. Replay
is read-only and is not a replacement for Transcript recovery.
