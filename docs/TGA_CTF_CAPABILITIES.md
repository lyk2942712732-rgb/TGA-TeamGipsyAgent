# Agent Session tools

At Session creation the runtime resolves concrete tools from the capability
registry and passes their JSON schemas directly to the configured model.

Built-in adapters include:

- `http.request`
- `workspace.read`, `workspace.write`, `workspace.shell`, `workspace.python`
- `artifact.inspect`
- `tool.invoke` for catalogued MCP methods
- `finish_session` supplied by the AgentSession host

Provider names replace dots with underscores (for example
`tga_http_request`), then map back to the registered implementation. Assistant
`tool_calls` and matching tool results remain in the same transcript. A tool
failure is returned to the Solver so it can repair its next action; it does not
become a Manager-created hypothesis or a policy-only terminal state.

The old `ActionSpec` executor remains an internal adapter and legacy test seam.
Normal model sessions do not emit or repair ActionSpec JSON.
