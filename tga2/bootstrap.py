"""The single composition root used by apps/api, CLI and tests."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import RLock

from tga2.agent.service import TaskRuntimeService
from tga2.config import Configuration
from tga2.integrations.mcp import (
    MCPConfig,
    MCPConfigRepository,
    MCPServer,
    load_mcp_tools,
)


class Container:
    def __init__(self, run_root: str | Path) -> None:
        self.run_root = Path(run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.configuration = Configuration(self.run_root / ".config")
        self.mcp = MCPConfigRepository(self.run_root / ".config" / "mcp.json")
        self.runtime = TaskRuntimeService(
            run_root=self.run_root, configuration=self.configuration
        )
        self.mcp_error: str | None = None
        self.mcp_errors: dict[str, str] = {}

    async def refresh_mcp_tools(self) -> list:
        loaded = []
        errors: dict[str, str] = {}
        for name, raw in self.mcp.list().items():
            if not raw.get("enabled", True):
                continue
            try:
                tools = await load_mcp_tools(
                    MCPConfig(servers={name: mcp_server_from_frontend(raw)})
                )
                selected = raw.get("enabledTools")
                if selected is not None:
                    allowed = set(selected)
                    tools = [
                        tool
                        for tool in tools
                        if tool.name in allowed
                        or any(tool.name.endswith(f"_{name}") for name in allowed)
                    ]
                loaded.extend(tools)
            except Exception as exc:  # noqa: BLE001 - retain healthy MCP servers
                errors[name] = str(exc)
        self.runtime.set_external_tools(loaded)
        self.mcp_errors = errors
        self.mcp_error = "; ".join(
            f"{name}: {message}" for name, message in errors.items()
        ) or None
        return loaded

    def refresh_mcp_tools_sync(self) -> list:
        return asyncio.run(self.refresh_mcp_tools())


_containers: dict[Path, Container] = {}
_lock = RLock()


def get_container(run_root: str | Path = "runs2") -> Container:
    root = Path(run_root).resolve()
    with _lock:
        if root not in _containers:
            _containers[root] = Container(root)
        return _containers[root]


def reset_containers() -> None:
    with _lock:
        _containers.clear()


def mcp_server_from_frontend(config: dict) -> MCPServer:
    transport = config.get("transport", "stdio")
    if transport == "stdio":
        stdio = config.get("stdio") or {}
        if stdio.get("source") == "docker_image":
            docker = stdio.get("docker") or {}
            args = ["run", "--rm", "-i"]
            if docker.get("readOnly", True):
                args.append("--read-only")
            if docker.get("capDropAll", True):
                args.extend(["--cap-drop", "ALL"])
            if docker.get("noNewPrivileges", True):
                args.extend(["--security-opt", "no-new-privileges"])
            if memory := docker.get("memory"):
                args.extend(["--memory", str(memory)])
            if cpus := docker.get("cpus"):
                args.extend(["--cpus", str(cpus)])
            if pids := docker.get("pidsLimit"):
                args.extend(["--pids-limit", str(pids)])
            args.extend(["--network", str(docker.get("network") or "none")])
            args.append(str(stdio.get("image")))
            return MCPServer(
                transport="stdio",
                command="docker",
                args=args,
            )
        return MCPServer(
            transport="stdio",
            command=stdio.get("command"),
            args=stdio.get("args") or [],
            env=stdio.get("env") or {},
        )
    http = config.get("http") or {}
    if http.get("verifyTls") is False:
        raise ValueError("TGA2 requires TLS certificate verification for remote MCP")
    headers = dict(http.get("headers") or {})
    for header, reference in (http.get("secretRefs") or {}).items():
        if not str(reference).startswith("env:"):
            raise ValueError("MCP secret references must use env:VARIABLE")
        variable = str(reference)[4:]
        if not variable or variable not in os.environ:
            raise ValueError(f"MCP secret environment variable is missing: {variable}")
        headers[str(header)] = os.environ[variable]
    return MCPServer(
        transport="streamable_http",
        url=http.get("url"),
        headers=headers,
    )


__all__ = ["Container", "get_container", "mcp_server_from_frontend", "reset_containers"]
