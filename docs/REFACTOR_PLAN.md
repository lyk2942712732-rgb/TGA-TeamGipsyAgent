# TGA AgentSession architecture migration

> Historical design note. Its proposal to remove scope and evidence authority is
> superseded by `ARCHITECTURE_IMPROVEMENT_PLAN.md` and the governed AgentSession
> implementation. The native tool loop remains, but current safety gates apply.

Status: product runtime migrated and locally validated  
Reference: `C:\Users\lyk\Desktop\黑客松冠军作品\BreachWeave`

## Decision

The previous refactor treated TGA's scope policy, intensity/risk switches,
mandatory evidence chain, flag/finding gates, API-v2 security semantics, and
Manager-created hypotheses as non-negotiable architecture. That decision is
withdrawn. It forced a Solver to request one synthetic `ActionSpec` at a time and
could stop a task before its first useful tool call.

The product runtime now follows BreachWeave's execution shape:

```text
Task files + optional Hint + execution boundaries
  -> Manager creates/resumes one Solver AgentSession
  -> model receives real tool definitions
  -> assistant emits native tool_calls
  -> tools execute in the Solver workspace
  -> matching tool results return to the same conversation
  -> repeat until finish_session, a result, cancellation, or turn limit
```

New schema-v4 Sessions do not accept a target URL/reference/MCP data source. Any
address, repository, account detail, or challenge text belongs in Hint or an
uploaded file. New Session retains explicit network, filesystem, process, rate,
concurrency, state-change, fuzzing, and containment boundaries. Old target and MCP
ACL fields remain readable only so existing task JSON and SQLite runs can open;
they do not decide schema-v4 product execution.

## Module mapping

| BreachWeave | TGA implementation | Responsibility |
| --- | --- | --- |
| `RuntimeManager` | `tga/runtime/manager.py` | Session lifecycle |
| `createSolverSession` | `tga/runtime/agent_session.py` | Transcript, workspace, native tool loop |
| pi agent tools | TGA registry exposed as function tools | Real tool definitions passed to the model |
| AgentSession events | ordered `AgentEvent` stream | Web/API replay |
| challenge manager | `TaskRuntimeService` + Manager | create/start/pause/resume/cancel |
| memory/ideas | compatibility board projection | Optional context, never an execution gate |

FastAPI and React do not implement a second executor. The legacy hypothesis planner
is reachable only when a test or integration explicitly injects a `Solver`; normal
configured-model API tasks use `AgentToolSession`.

## Event and recovery contract

The native timeline uses:

- `MESSAGE_START` / `MESSAGE_END`
- `TOOL_EXECUTION_START` / `TOOL_EXECUTION_END`
- `AGENT_ERROR` / `AGENT_FINISHED`
- `FLAG_FOUND`
- `SESSION_STARTED` / `SESSION_STOPPED` / `SESSION_CONTROLLED`

Assistant tool calls and matching `tool_call_id` results are persisted in
`solvers/<solver-id>/session/messages.json`. Resume reopens this transcript and the
same workspace. Provider-bound strings are normalized to Unicode scalar values, so
an unpaired UTF-16 surrogate cannot reproduce the failure from
`task_fc7dbe693dfd`.

## Frontend migration

New Session contains only mode/task configuration, task files, optional Hint text
and attachments, and general execution boundaries. It has no URL/reference/MCP
Resource/Tool input or task-level MCP authorization. The summary shows globally
enabled/reachable or discovered MCP services read-only. Dashboard and Runtime
describe Agent turns, tools, artifacts, and results.

## Compatibility removal boundary

After old fixtures and integrations use native AgentSession events, remove the
legacy `LLMRuntimeSolver.propose_action` path, pseudo-Solver fan-out, required
hypothesis assignment, semantic retry gates, and compatibility-only policy fields.
Until then, those readers and test seams may remain but must not regain product
authority.

## Acceptance criteria

- A configured model receives native tools and can make consecutive tool calls in
  one persistent transcript.
- Execution starts without a prebuilt hypothesis or role fan-out.
- Pause/resume/cancel and reload use the same Session state.
- Runtime renders model messages and tool start/end events in order.
- Task creation uses staged asset IDs and no target/reference/MCP grant fields.
- Every schema-v4 Session uses one persistent workspace shared with local Docker MCPs.
- Globally enabled MCPs are automatic for new Sessions; live disable blocks old Sessions.
- Malformed Unicode input is repaired before provider serialization.
- Python tests, frontend tests/build, and local API/UI fixtures pass.
