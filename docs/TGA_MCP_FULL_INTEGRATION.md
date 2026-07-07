# TGA MCP Full Integration

Developer B owns the execution layer. TGA connects the full FuzzingLabs `mcp-security-hub`
checkout as a capability catalog, but every execution is still gated by TGA scope,
intensity, active-scan, rate-limit, and artifact rules.

## Model

TGA separates three states:

- `registered`: discovered from a local `mcp-security-hub` checkout.
- `available`: the Docker image exists locally and can be invoked.
- `allowed`: the concrete task target passes TGA policy.

This keeps full integration from becoming full permission.

## Bootstrap

Clone the upstream hub outside the TGA package:

```powershell
git clone https://github.com/FuzzingLabs/mcp-security-hub.git D:\CTF\mcp-security-hub
$env:TGA_MCP_SECURITY_HUB_ROOT = 'D:\CTF\mcp-security-hub'
```

Inspect the full catalog:

```powershell
python scripts\tga_mcp_catalog.py --hub-root $env:TGA_MCP_SECURITY_HUB_ROOT --summary
```

Build upstream images as needed:

```powershell
docker compose -f "$env:TGA_MCP_SECURITY_HUB_ROOT\docker-compose.yml" build
```

Or let TGA build iteratively with retries and JSON reporting:

```powershell
python scripts\tga_mcp_bootstrap.py --hub-root $env:TGA_MCP_SECURITY_HUB_ROOT --build --retries 3
```

To start with a small validation set while keeping the full catalog registered:

```powershell
python scripts\tga_mcp_bootstrap.py --hub-root $env:TGA_MCP_SECURITY_HUB_ROOT --build --only nmap nuclei gitleaks --retries 3
```

For long-running autonomous iteration, keep a report artifact and avoid resetting the
checkout every time:

```powershell
python scripts\tga_mcp_bootstrap.py `
  --hub-root $env:TGA_MCP_SECURITY_HUB_ROOT `
  --no-fetch `
  --build `
  --network-profile cn `
  --retries 5 `
  --timeout-seconds 1200 `
  --report-path runs\mcp-bootstrap-report.json
```

`--network-profile cn` builds from a temporary patched copy of the hub and injects
package-manager mirror hints for apk, pip, and npm, plus a Go toolchain fallback
that avoids direct `go.dev` tarball downloads. It does not modify the source checkout.

Check availability:

```powershell
python scripts\tga_mcp_healthcheck.py --hub-root $env:TGA_MCP_SECURITY_HUB_ROOT
```

Run safe stdio smoke calls against a representative set of MCP servers:

```powershell
python scripts\tga_mcp_smoke.py `
  --hub-root $env:TGA_MCP_SECURITY_HUB_ROOT `
  --timeout-seconds 120 `
  --report-path runs\mcp-smoke-default-final.json
```

## Current Verification

Verified on 2026-07-07 against `fuzzingLabs/mcp-security-hub`
revision `b6800740da9965e9dd3fde2ec3cf4c775c358f72`:

- Catalog discovery: 42 MCP servers registered.
- Image availability: 42/42 images present locally.
- Safe stdio smoke: 5/5 representative calls passed
  (`searchsploit`, `solazy`, `yara`, `nmap`, `boofuzz`).
- Test suite: `17 passed`.

Build evidence is stored under `runs\mcp-bootstrap-*.json`; the final smoke report
is `runs\mcp-smoke-default-final.json`.

## Execution Rules

`ToolRunner.run_tool(...)` always:

1. Resolves the requested tool through the full catalog.
2. Checks `tool_policy.is_allowed(...)`.
3. Applies a per-task/tool/target rate limit.
4. Invokes MCP over stdio, preferring `docker compose run` from the hub.
5. Saves stdout, stderr, command, status, timestamps, and raw result as an artifact.

Policy currently allows passive tools in passive mode, active tools only when
`allow_active_scan=true`, and registers destructive/high-blast-radius tools while
blocking execution until the shared task contract grows an explicit flag for them.
