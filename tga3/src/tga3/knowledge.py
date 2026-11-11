"""Native MCP connector for the baked offline CTF Wiki only."""
import os
from typing import Literal

DEFAULT_MCP_URL = ""
SERVER_NAME = "ctf_wiki"
KnowledgeSource = Literal["none", "local"]

def worker_knowledge_name():
    return SERVER_NAME

def worker_knowledge_server():
    if os.environ.get("TGA3_AGENT_ROLE") != "worker" or os.environ.get("TGA3_KNOWLEDGE_SOURCE") != "local":
        return None
    url = os.environ.get("TGA3_CTF_KNOWLEDGE_MCP_URL", "")
    # Hosted workers share the controller network namespace; external endpoints are never accepted.
    if url != "http://127.0.0.1:8083/local-knowledge-mcp/":
        return None
    return {"url": url}
