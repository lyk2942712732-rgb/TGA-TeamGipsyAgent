"""Deterministic line-delimited MCP stdio fixture used by CI."""

from __future__ import annotations

import json
import sys


TOOLS = [
    {
        "name": "echo",
        "description": "Echo a required text value.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"text": {"type": "string"}, "repeat": {"type": "integer", "minimum": 1}, "token": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "large_result",
        "description": "Return a deterministic large text result.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"chars": {"type": "integer", "minimum": 1}},
            "required": ["chars"],
        },
    },
]


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tga-test-mcp", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        arguments = params.get("arguments") or {}
        if params.get("name") == "echo":
            text = str(arguments.get("text") or "") * int(arguments.get("repeat") or 1)
        else:
            text = "x" * int(arguments.get("chars") or 1)
        print("fixture diagnostic", file=sys.stderr, flush=True)
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
