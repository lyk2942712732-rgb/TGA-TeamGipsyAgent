# Target architecture

The target architecture is now the current architecture:

```text
apps -> application -> runtime -> domain
                         ^
                         |
              infrastructure implements ports
```

One `TaskOrchestrator` owns each Task. A Supervisor owns the `GlobalPlan`; Workers own
their assigned `LocalPlan` and submit structured results; Reviewer and Reporter duties are
role-scoped. Every Solver has a durable identity, private Transcript, budget, ToolPolicy,
Skill snapshot, and at most one active runner.

`ToolGovernanceGateway` owns Action authorization, approval, lifecycle, persistent budget,
idempotency, resource locks, routing, and bounded observations. Concrete transports perform
I/O only after a governed Action has been validated and revalidated.

SQLite is authoritative for schema-v6 Tasks, orchestration, events, and projections. The
browser consumes bounded Commands and Queries and never infers authority from Transcript
text. Replay reduces canonical schema-v6 events into the same normalized frontend store.

Schema v5 is outside the architecture at runtime. It can only be opened by the explicit
offline migration package, which creates backups and publishes a verified schema-v6 copy.
