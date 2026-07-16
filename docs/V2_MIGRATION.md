# Migration from the hypothesis runtime to AgentSession

## Product path

`POST /api/v2/tasks` stores the task, creates a Session, and schedules the
Manager. With a configured model, Manager launches `AgentToolSession`. One main
Solver owns:

- `runs/<task>/solvers/<solver>/workspace/`
- `runs/<task>/solvers/<solver>/session/messages.json`
- the native model/tool transcript and tool event stream

The previous automatic recon/targeted/research fan-out is not used. Ideas,
Memory, SQLite action tables, and old AgentEvents remain readable so existing
runs can be opened.

## Resume

Resume reuses the main Solver id, transcript, workspace, and turn count. New
hints are appended as user messages. Interrupted model/provider requests leave
the Session blocked with an `AGENT_ERROR` event and can be retried after the
provider configuration is repaired.

## Compatibility deletion

The explicit injected-Solver path remains for old tests and integrations. It
can be removed after those fixtures consume native message/tool events. It is
not a product fallback when a configured model is present.
