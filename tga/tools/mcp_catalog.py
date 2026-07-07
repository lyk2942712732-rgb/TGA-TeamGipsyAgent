"""MVP MCP tool catalog."""

MVP_MCP_TOOLS = {
    "recon": ["nmap", "whatweb"],
    "web": ["nuclei", "ffuf", "sqlmap"],
    "code": ["semgrep", "gitleaks"],
    "binary": ["binwalk", "radare2"],
}

ACTIVE_TOOLS = {"nmap", "nuclei", "ffuf", "sqlmap"}
PASSIVE_TOOLS = {"whatweb", "semgrep", "gitleaks", "binwalk", "radare2"}


def all_tools() -> list[str]:
    tools: set[str] = set()
    for names in MVP_MCP_TOOLS.values():
        tools.update(names)
    return sorted(tools)

