# Schema-v5 Session input and execution-policy migration

## New write contract

`POST /api/v2/tasks` has one product write path. New Sessions use task schema 5
and accept one initial user prompt, one undifferentiated list of staged input
files, mode configuration, and the four-part execution policy.

```json
{
  "id": "task_optional_client_id",
  "name": "Analyze sample",
  "mode": "reverse_engineering",
  "goal": "Recover the requested behavior",
  "modeOptions": {"mode": "reverse_engineering"},
  "input": {
    "text": "Optional initial task input, URLs, and code fragments",
    "fileIds": ["asset_<32 hex>"]
  },
  "executionPolicy": {}
}
```

The old `{ "task": TGATask }` creation envelope is not a parallel write path.
`targetUrls`, references, MCP Resources/Tools, task/session MCP grants, and all
other removed fields are rejected with HTTP 422. The runtime does not normalize,
ignore, log, or execute removed request fields.

## Two-stage file lifecycle

1. `POST /api/v2/input-uploads?filename=...` streams one body to
   `runs/_input_staging/<token>` and returns an asset ID plus detected metadata.
2. Session creation verifies every ID, filename, size, SHA-256, count, and total
   size while copying into a newly created Session workspace.
3. A successful transaction deletes claimed staging directories. A failed
   transaction removes its partial Session root but leaves staging retryable.
4. Unclaimed staging is swept after `TGA_INPUT_STAGING_TTL_SECONDS` (24 hours by
   default) when another upload begins.

Browser MIME is stored only as client metadata. Signature/filename detection is
authoritative. Stored names derive from backend asset IDs, so traversal and
same-name overwrite are impossible.

## Workspace layout

```text
runs/<session-id>/workspace/
  inputs/files/
  artifacts/
  evidence/
  tool-results/
  state/input-manifest.json
```

Original inputs are immutable. Agent tools verify their saved size and SHA-256
before read, search, view, or materialization. `input_materialize` returns the
existing `/workspace/inputs/...` path; derived content belongs in
`/workspace/artifacts`.

## Additive SQLite migration

Opening an older database applies additive `ALTER TABLE` migrations so its
metadata can be inspected and migrated without destructive rewrites:

- `sessions.schema_version` defaults to 2;
- `sessions.workspace_path` defaults to an empty string;
- `sessions.mcp_catalog_version` defaults to an empty string;
- existing runtime tables/columns continue to receive required additive upgrades.

New schema-v5 rows persist `workspace_path = "workspace"`, the MCP catalog
snapshot, normalized network seed origins, and a credential-free model
snapshot. The live Runtime API and Manager reject every task schema other than
5 with `SCHEMA_VERSION_UNSUPPORTED`; there is no legacy UI projection,
execution fallback, or dual write. An operator must run the explicit offline
v4-to-v5 migration before an old task can execute.

## Model context

`SessionContextBuilder` produces the first auditable user message with mode,
Hint, file paths and metadata, MCP creation snapshot, workspace rules, execution
boundaries, and completion criteria. Supported/unknown vision models receive
real bounded image content blocks. Explicit text-only models receive image paths
and image-analysis/OCR guidance. Large text, archives, and binary inputs are not
inlined.

## MCP policy

Task-level MCP ACL fields may remain as inert JSON in an old database selected
for offline migration. `TGATask` and the live runtime do not parse or authorize
them. For current Sessions:

- creation snapshots globally configured/enabled services that are discovered
  or not explicitly unavailable;
- only discovered methods become callable routes;
- the snapshot is audit/visibility state, not a user grant;
- newly added services are visible only to newly created Sessions;
- globally disabling/removing a service blocks the next call from every Session;
- network, filesystem, process, rate, concurrency, state-change, and containment
  gates remain authoritative;
- destructive MCP methods still require exact
  `mcp:<server>.<method>` state-change authorization.

Docker MCP calls mount the Session workspace as `/workspace:ro` and mount
`/workspace/artifacts` read-write. Streamable HTTP MCP services have no local
mount and are reported as remote.
