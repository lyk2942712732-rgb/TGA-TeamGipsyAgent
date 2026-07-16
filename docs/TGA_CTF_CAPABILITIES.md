# TGA CTF Capability Executor

This document describes the Developer B week 2 capability layer. The layer
does not choose exploit paths and does not submit flags. It only executes
approved `ActionSpec` requests inside bounded CTF/workspace controls and
returns reproducible `ActionResult` records with artifacts.

## Scope

Implemented capabilities:

- `http.request`
- `tool.invoke`
- `workspace.python`
- `workspace.binary`
- `artifact.inspect`

Intentionally not implemented:

- `challenge.submit_flag`
- `challenge.get_state`
- ChallengeBridge or any target flag submission interface

Candidate flags found in outputs are returned in `ActionResult.candidate_flags`
for the server-side flag gate owned outside this layer.

## Registry

Use the registry script to inspect available capabilities:

```powershell
py -3.13 scripts\tga_capability_registry.py --project-root .
```

The MCP Hub checkout is resolved in this order:

1. `TGA_MCP_SECURITY_HUB_ROOT`
2. `<project-root>\mcp-security-hub`

This keeps the capability layer portable and avoids dependencies on a
developer-specific Desktop path.

Each registry descriptor includes:

- input schema
- risk level
- supported modes
- max output bytes
- timeout
- scope validator description
- budget key
- redacted summary format
- availability state

## Execution Model

`CapabilityExecutor` creates a solver-specific run directory:

```text
runs/<task_id>/solvers/<solver_id>/
```

The executor writes scripts, temporary files, and artifacts under that solver
directory. One solver cannot inspect another solver's artifacts through
`artifact.inspect`.

Every block, timeout, or failure is artifacted. The executor returns
`ActionResult(status="blocked" | "timeout" | "failed")` instead of raising for
normal policy outcomes.

## Capability Controls

`http.request`

- Allows only `http` and `https`.
- Rejects `ws` and `wss`.
- Enforces origin scope on the requested URL.
- Validates redirects before following them.
- Blocks attachment and octet-stream downloads.
- Redacts sensitive request headers in artifacts.

`tool.invoke`

- Uses the project-relative MCP Hub checkout or `TGA_MCP_SECURITY_HUB_ROOT`.
- Resolves tools from the local Hub catalog.
- Validates the requested MCP method and JSON input schema before execution.
- Reuses the existing TGA MCP policy and runner path.

`workspace.python`

- Runs code in the solver workspace with `python -I`.
- Uses a minimal environment and no shell.
- Installs a runtime audit hook that denies network, subprocess, ctypes,
  registry access, and file reads/writes outside allowed roots.
- Enforces timeout and output truncation.
- Blocks obvious network, subprocess, host secret, registry, and absolute drive
  path access patterns.

`workspace.binary`

- Performs passive local binary inspection only.
- Reads files from the solver workspace or explicitly scoped local attachment
  roots.
- Supports `metadata`, `strings`, and `hexdump`.

`artifact.inspect`

- Reads artifacts from the current solver artifact directory only.
- Supports byte ranges, keyword hit summaries, truncation, and candidate flag
  extraction.

## Test Coverage

The tests cover:

- registry descriptors and absence of challenge submission capabilities
- HTTP scope, redirects, downloads, truncation, and timeouts
- Python workspace execution, blocking, timeouts, and truncation
- passive binary metadata, strings, and hexdump inspection
- solver-scoped artifact inspection
- MCP Hub method and schema validation
