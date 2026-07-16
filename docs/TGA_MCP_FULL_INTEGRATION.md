# MCP integration

MCP servers are discovered from `mcp-security-hub`. Catalogued method names and
input schemas are exposed to the Solver through the Session tool catalog. The
generic `tool.invoke` adapter executes the selected server method and returns
stdout/stderr, status, and Artifact references to the same AgentSession turn.

Availability has two states that matter to the product:

1. catalogued: the server and method definition were discovered;
2. runnable: the local Docker/process dependency is available.

There is no New Session switch for scan intensity or active-scan permission.
The task target is the Session contract. Timeouts and output bounds remain
runtime stability controls, not planning gates.

Use:

```powershell
python scripts\tga_mcp_catalog.py --hub-root .\mcp-security-hub --summary
python scripts\tga_mcp_healthcheck.py --hub-root .\mcp-security-hub
```
