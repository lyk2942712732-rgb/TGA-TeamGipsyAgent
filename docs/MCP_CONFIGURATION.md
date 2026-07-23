# MCP configuration and runtime

TGA uses one host-controlled `mcp.json` allowlist for both STDIO and MCP
Streamable HTTP servers. The runtime never scans Docker or implicitly pulls an
image. Every discovered method becomes a native AgentSession function named
`mcp__<server>__<method>`.

New Session creation never selects MCP services or methods. The global registry
is the only MCP management/enablement source. A schema-v4 Session records the
globally enabled catalog and discovered methods visible at creation for audit;
this snapshot is not a user ACL.

## Architecture

```text
mcp.json
  -> validated transport config
  -> initialize / notifications/initialized / tools/list
  -> immutable per-turn native tool catalog
  -> tools/call in the same AgentSession
  -> bounded result + task Artifact + ordered events
```

The configuration, transport, lifecycle/catalog, policy, AgentSession adapter,
and Artifact persistence are separate layers under `tga/tools/` and
`tga/runtime/`. An MCP service cannot select its own Docker arguments, mounts,
credentials, tool name, risk level, or task scope.

## Configuration

Set a deployment-specific file when necessary:

```powershell
$env:TGA_MCP_CONFIG_PATH = "C:\path\to\mcp.json"
```

Docker STDIO example:

```json
{
  "version": 1,
  "maxConcurrency": 4,
  "servers": {
    "nmap": {
      "enabled": true,
      "transport": "stdio",
      "stdio": {
        "source": "docker_image",
        "image": "nmap-mcp:latest",
        "environment": {},
        "secretRefs": {},
        "docker": {
          "memory": "1g",
          "cpus": 1.0,
          "pidsLimit": 256,
          "network": "none",
          "readOnly": true,
          "capDropAll": true,
          "capAdd": [],
          "noNewPrivileges": true,
          "tmpfs": {
            "/tmp": "rw,noexec,nosuid,size=64m,mode=1777",
            "/app/output": "rw,noexec,nosuid,size=128m,mode=1777"
          }
        }
      },
      "enabledTools": ["quick_scan"]
    }
  }
}
```

Streamable HTTP example:

```json
{
  "version": 1,
  "servers": {
    "remote-scanner": {
      "enabled": true,
      "transport": "streamable_http",
      "http": {
        "url": "https://mcp.example.com/mcp",
        "verifyTls": true,
        "headers": {"X-Client": "tga"},
        "secretRefs": {"Authorization": "env:MCP_SCANNER_TOKEN"},
        "proxyUrl": null,
        "allowSameOriginRedirects": false,
        "maxRetries": 1
      },
      "enabledTools": ["scan", "status"]
    }
  }
}
```

`stdio` and `http` are mutually exclusive. Legacy flat STDIO entries are read
and normalized for backward compatibility. `enabledTools` is an allowlist; an
empty list means all discovered tools. Sensitive HTTP headers and process
environment values must use `env:VARIABLE` references. Secret values are
resolved only when connecting and are never returned by an API.

## STDIO image import

The management page accepts only Docker image archives created by
`docker save` (`.tar`, `.tar.gz`, or `.tgz`). Source ZIP files, Dockerfiles,
arbitrary build contexts, links, devices, and path traversal are not accepted.
The browser displays byte upload progress and can cancel an in-progress upload.
The temporary file is removed on success, error, or client disconnect.

TGA runs `docker load`, then `docker image inspect`. If an archive contains one
RepoTag it is configured automatically. If it contains multiple RepoTags, the
API returns all candidates and no server is written until the operator selects
one. Entering an existing image name runs only `docker image inspect`; it never
performs an implicit pull.

The host generates `docker run --rm -i` and all memory, CPU, PID, network,
read-only-root, capability, no-new-privileges, tmpfs, environment, and workspace
mount arguments. Uploaded bytes cannot override these controls.

Workspace access is automatic and is not an operator setting. Catalog discovery
runs without a task mount. During a real task call, Docker MCP servers receive
the persistent Session workspace at `/workspace` read-only and the dedicated
`/workspace/artifacts` directory read-write. Use `input_materialize` before a
file-oriented MCP call and pass the returned `mcp_path`; host Windows paths are
never valid container paths. Streamable HTTP MCP servers never receive a local
filesystem mount and health reports them as `mode: remote` with
`mounted_on_task_call: false`. TGA does not claim remote file access unless the
protocol explicitly transfers content.

## Streamable HTTP behavior

Requests advertise `application/json` and `text/event-stream`. TGA accepts a
single JSON-RPC object, a JSON batch, and multiple SSE `data:` messages. It
captures `MCP-Session-Id`, sends the negotiated `MCP-Protocol-Version` on later
requests, sends DELETE when closing a session, and permits one bounded
reinitialization after a 404/410 expired-session response.

TLS verification defaults to true. Redirects default to blocked; the optional
relaxation permits same-origin redirects only. Ambient host proxy variables are
ignored; a proxy must be explicit in `proxyUrl`. Credentials embedded in URLs
or plaintext sensitive headers are rejected.

## Management UI

Settings → Capabilities and MCP provides:

1. transport selection;
2. Docker archive/existing image or HTTP endpoint parameters;
3. `initialize` and `tools/list` connection test while the service remains disabled;
4. tool allowlist selection and final enable/save.

Configured services are grouped and collapsible. Each card displays transport,
health, protocol/error information and grouped tool descriptions/schemas. Edit,
Enable/Disable, Refresh, and Delete modify the host config. Delete removes only
the allowlist entry and deliberately retains the local image.

## API

```text
GET    /api/v2/mcp/servers
POST   /api/v2/mcp/servers
GET    /api/v2/mcp/servers/{id}
PATCH  /api/v2/mcp/servers/{id}
DELETE /api/v2/mcp/servers/{id}
POST   /api/v2/mcp/servers/{id}/test
POST   /api/v2/mcp/servers/{id}/refresh
GET    /api/v2/mcp/servers/{id}/tools
POST   /api/v2/mcp/images/import
GET    /api/v2/mcp/images
POST   /api/v2/mcp/images/{image}/inspect
```

Legacy `/api/v2/tools/mcp/*` refresh/import/enable/delete routes remain during
migration. Management responses redact URL queries and never contain resolved
secrets.

## Discovery, execution, and failure states

Health distinguishes configured, reachable, discovered, and runnable. Records
include transport, protocol version, server info, discovery time, redacted
endpoint or image, tool count, and typed error details. Important error codes
include `CONFIG_ERROR`, `TRANSPORT_START_FAILED`, `HTTP_CONNECT_FAILED`,
`TLS_ERROR`, `AUTH_ERROR`, `HTTP_REDIRECT_BLOCKED`, `MCP_INITIALIZE_FAILED`,
`DISCOVERY_ERROR`, `TIMEOUT`, `MCP_TOOL_ERROR`, and `OUTPUT_TRUNCATED`.

Each Agent turn retains an immutable catalog snapshot. Policy validates the
discovered input schema, task mode/risk, rate limits, and concurrency again
before `tools/call`. Small results retain native content blocks. Large results
are bounded and written as task-scoped `.mcp.json` Artifacts with explicit
saved/original byte counts and truncation state.

Schema-v4 lifecycle behavior is deliberate:

- configured, globally enabled services that are discovered or at least not
  explicitly unavailable are listed in a new Session's creation snapshot;
- only methods with discovered routes are callable;
- services added after creation do not enter an existing Session's snapshot;
- a global disable/removal is reloaded before `tools/call` and immediately
  rejects calls from existing Sessions;
- no per-task service/tool grant, MCP Resource grant, or Session MCP checkbox
  participates in schema-v4 authorization;
- passive/active/destructive calls still obey general execution boundaries;
  destructive calls require exact `mcp:<server>.<method>` state-change
  authorization.

## Verification

```powershell
python scripts\tga_mcp_catalog.py --config $env:TGA_MCP_CONFIG_PATH
python scripts\tga_mcp_healthcheck.py --config $env:TGA_MCP_CONFIG_PATH
python scripts\tga_mcp_smoke.py --config $env:TGA_MCP_CONFIG_PATH `
  --server nmap --tool quick_scan --arguments '{"target":"127.0.0.1"}'

python -m pytest -q
cd apps\web
npm test
npm run build
```

Tests cover legacy/new configuration, schema-v4 creation snapshots and live
disable/new-service isolation, STDIO discovery/call/timeout/cleanup,
Docker archive import and multiple RepoTags, HTTP JSON/SSE/session headers and
cleanup, credential rules, allowlist filtering, full management CRUD, native
Agent tool execution, Artifact handling, and frontend service controls.

## Known operational risks

- Docker images and remote MCP endpoints execute operator-trusted code; review
  them before enabling.
- Disabling TLS verification or enabling Docker networking is an explicit
  per-server relaxation and should be rare.
- A remote server can be slow or malformed; hard timeouts, output limits,
  concurrency limits, and typed failures prevent unbounded execution but do not
  make an untrusted service safe.
- The local Docker image is retained after configuration deletion. Remove it
  separately only when no other project needs it.
