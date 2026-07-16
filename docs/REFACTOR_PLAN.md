# TGA AgentSession architecture migration

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
Task target + goal + hint
  -> Manager creates/resumes one Solver AgentSession
  -> model receives real tool definitions
  -> assistant emits native tool_calls
  -> tools execute in the Solver workspace
  -> matching tool results return to the same conversation
  -> repeat until finish_session, a result, cancellation, or turn limit
```

`target` is the Session's target contract. New Session no longer asks for execution
intensity, active-scan permission, a separate scope list, or a TLS-policy switch.
Old fields remain readable only so existing task JSON and SQLite runs can open; they
do not decide normal product execution.

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

New Session contains only the session name/mode, target URL or path, goal, theme,
description, optional flag format, and initial hint. Dashboard and Runtime describe
Agent turns, tools, artifacts, and results; they no longer expose risk budgets,
active-scan state, or scope counts.

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
- Task creation works without scope, intensity, or active-scan fields.
- Malformed Unicode input is repaired before provider serialization.
- Python tests, frontend tests/build, and local API/UI fixtures pass.

