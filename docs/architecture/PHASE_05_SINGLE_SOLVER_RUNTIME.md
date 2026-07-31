# Phase 5 Solver runtime

> Historical phase record, updated to describe the final multi-Solver implementation.

`SolverRunner` loads one durable `SolverInstance` and runs that identity's model-turn engine.
It does not choose identities or provide a single-Solver compatibility path. Formal Tasks are
created and coordinated by `TaskOrchestrator`, with role-specific Supervisor, Worker,
Reviewer, and Reporter instances.

`ContextBuilder` selects bounded schema-v6 TaskSpec, Hint, Skill, plan, Knowledge, Retrieval,
and per-Solver Transcript state. It has no Memory or StrategyCard fallback.

Workers call `submit_worker_result`; Supervisors call `propose_task_completion`; the Host
performs deterministic completion validation. Every executable model tool call crosses the
governance gateway before reaching an execution adapter.
