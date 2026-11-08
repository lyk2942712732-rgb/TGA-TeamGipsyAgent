"""Direct native HTTP MCP wiring for the ctf42 challenge archive."""

from __future__ import annotations

import os

DEFAULT_MCP_URL = "https://ctf42.online/mcp"
SERVER_NAME = "ctf42"


def worker_knowledge_server() -> dict | None:
    url = os.environ.get("TGA3_CTF_KNOWLEDGE_MCP_URL", "").strip()
    if os.environ.get("TGA3_AGENT_ROLE") != "worker" or not url:
        return None
    return {"url": url}
