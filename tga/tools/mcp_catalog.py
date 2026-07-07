from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


IGNORED_TOP_LEVEL = {
    ".git",
    ".github",
    "examples",
    "scripts",
    "tests",
}


class MCPToolSpec(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPServerSpec(BaseModel):
    id: str
    category: str
    path: str
    dockerfile: str | None = None
    image: str
    compose_service: str | None = None
    profiles: list[str] = Field(default_factory=list)
    implemented: bool = False
    wrapper: bool = False
    tools: list[MCPToolSpec] = Field(default_factory=list)
    source: str = "mcp-security-hub"

    @property
    def short_name(self) -> str:
        return self.id.removesuffix("-mcp")


class MCPCatalog(BaseModel):
    hub_root: str
    servers: list[MCPServerSpec] = Field(default_factory=list)
    revision: str | None = None

    def get(self, name: str) -> MCPServerSpec | None:
        needle = normalize_tool_name(name)
        for server in self.servers:
            names = {server.id, server.short_name, normalize_tool_name(server.id), normalize_tool_name(server.short_name)}
            if needle in names:
                return server
        return None

    def resolve_server_for_tool(self, tool: str) -> MCPServerSpec | None:
        direct = self.get(tool)
        if direct:
            return direct
        needle = normalize_tool_name(tool)
        for server in self.servers:
            for item in server.tools:
                if normalize_tool_name(item.name) == needle:
                    return server
        return None


def normalize_tool_name(value: str) -> str:
    return value.lower().replace("_", "-").removesuffix("-mcp")


def discover_mcp_security_hub(root: str | Path) -> MCPCatalog:
    hub_root = Path(root).expanduser().resolve()
    if not hub_root.exists():
        raise FileNotFoundError(f"mcp-security-hub root does not exist: {hub_root}")

    compose_services = _parse_compose_services(hub_root / "docker-compose.yml")
    servers: list[MCPServerSpec] = []
    for server_dir in _iter_server_dirs(hub_root):
        server_id = server_dir.name
        category = server_dir.parent.name
        compose = compose_services.get(server_id, {})
        server_py = server_dir / "server.py"
        dockerfile = server_dir / "Dockerfile"
        tools = parse_server_tools(server_py) if server_py.exists() else parse_readme_tools(server_dir / "README.md")
        image = str(compose.get("image") or f"{server_id}:latest")
        servers.append(
            MCPServerSpec(
                id=server_id,
                category=category,
                path=server_dir.relative_to(hub_root).as_posix(),
                dockerfile=dockerfile.relative_to(hub_root).as_posix() if dockerfile.exists() else None,
                image=image,
                compose_service=server_id if server_id in compose_services else None,
                profiles=list(compose.get("profiles", [])),
                implemented=server_py.exists(),
                wrapper=not server_py.exists(),
                tools=tools,
            )
        )

    servers.sort(key=lambda item: (item.category, item.id))
    return MCPCatalog(hub_root=str(hub_root), servers=servers, revision=_read_git_revision(hub_root))


def parse_server_tools(server_py: str | Path) -> list[MCPToolSpec]:
    path = Path(server_py)
    if not path.exists():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    tools: list[MCPToolSpec] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) != "Tool":
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        name = _literal_string(kwargs.get("name"))
        if not name:
            continue
        description = _literal_string(kwargs.get("description"))
        input_schema = _literal_dict(kwargs.get("inputSchema")) or _literal_dict(kwargs.get("input_schema")) or {}
        tools.append(MCPToolSpec(name=name, description=description, input_schema=input_schema))
    return tools


def parse_readme_tools(readme: str | Path) -> list[MCPToolSpec]:
    path = Path(readme)
    if not path.exists():
        return []
    tools: list[MCPToolSpec] = []
    in_tools_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^##+\s+Tools\s*$", line, re.IGNORECASE):
            in_tools_section = True
            continue
        if in_tools_section and line.startswith("#"):
            break
        if not in_tools_section or not line.startswith("|"):
            continue
        columns = [part.strip().strip("`") for part in line.strip().strip("|").split("|")]
        if len(columns) < 2:
            continue
        name, description = columns[0], columns[1]
        if not name or name.lower() in {"tool", "server"} or set(name) <= {"-"}:
            continue
        tools.append(MCPToolSpec(name=name, description=description or None))
    return tools


def _iter_server_dirs(root: Path) -> list[Path]:
    server_dirs: list[Path] = []
    for category_dir in root.iterdir():
        if not category_dir.is_dir() or category_dir.name in IGNORED_TOP_LEVEL:
            continue
        for child in category_dir.iterdir():
            if not child.is_dir():
                continue
            if (child / "Dockerfile").exists() and (child.name.endswith("-mcp") or child.name == "mcp-scan"):
                server_dirs.append(child)
    return server_dirs


def _parse_compose_services(compose_path: Path) -> dict[str, dict[str, str]]:
    if not compose_path.exists():
        return {}
    services: dict[str, dict[str, str]] = {}
    in_services = False
    current: str | None = None
    for raw_line in compose_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^[A-Za-z0-9_-]+:\s*$", line):
            if line.startswith("services:"):
                in_services = True
                current = None
                continue
            if in_services:
                break
        if not in_services:
            continue
        service_match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if service_match:
            current = service_match.group(1)
            services[current] = {}
            continue
        if current is None:
            continue
        image_match = re.match(r"^\s+image:\s*['\"]?([^'\"]+)['\"]?\s*$", line)
        if image_match:
            services[current]["image"] = image_match.group(1)
            continue
        context_match = re.match(r"^\s+context:\s*['\"]?([^'\"]+)['\"]?\s*$", line)
        if context_match:
            services[current]["context"] = context_match.group(1)
            continue
        profiles_match = re.match(r"^\s+profiles:\s*\[(.+)]\s*$", line)
        if profiles_match:
            profiles = [
                item.strip().strip("'\"")
                for item in profiles_match.group(1).split(",")
                if item.strip()
            ]
            services[current]["profiles"] = profiles
    return services


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_string(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _literal_dict(node: ast.AST | None) -> dict[str, Any] | None:
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _read_git_revision(root: Path) -> str | None:
    head = root / ".git" / "HEAD"
    if not head.exists():
        return None
    value = head.read_text(encoding="utf-8").strip()
    if value.startswith("ref: "):
        ref = root / ".git" / value.removeprefix("ref: ")
        if ref.exists():
            return ref.read_text(encoding="utf-8").strip()
    return value or None
