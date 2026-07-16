# Runtime report projection

Reports are generated from the same Session snapshot used by Web and CLI.
They summarize:

- task name, mode, target, and goal;
- Session and Solver lifecycle;
- native model message and tool execution events;
- tool calls, results, errors, and referenced Artifacts;
- results found by the Solver;
- final status and stop reason.

Older runs may also contain hypotheses, Memory, findings, or legacy gate events.
The report can render these for historical readability but does not present them
as requirements of the AgentSession architecture.
