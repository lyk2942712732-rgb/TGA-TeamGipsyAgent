from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tga.contracts import ArtifactRecord, Intent, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.tools import tool_policy
from tga.tools.mcp_catalog import MCPCatalog, MCPServerSpec
from tga.tools.mcp_client import MCPClient
from tga.tools.rate_limit import RateLimiter


class ToolRunner:
    def __init__(
        self,
        *,
        catalog: MCPCatalog,
        artifact_store: ArtifactStore,
        mcp_client: MCPClient | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        self.catalog = catalog
        self.artifact_store = artifact_store
        self.mcp_client = mcp_client or MCPClient(hub_root=catalog.hub_root)
        self.rate_limiter = rate_limiter or RateLimiter()

    def run_tool(
        self,
        *,
        task: TGATask,
        intent: Intent,
        tool: str,
        target: str,
        args: dict[str, Any],
        max_output_bytes: int = 262_144,
    ) -> ArtifactRecord:
        server = self.catalog.resolve_server_for_tool(tool)
        if server is None:
            return self._save_error(task, intent, tool, target, "TOOL_NOT_AVAILABLE", "tool is not registered")

        decision = tool_policy.is_allowed(
            tool=server.id,
            target=target,
            scope=task.scope,
            intensity=task.intensity,
            allow_active_scan=task.allow_active_scan,
        )
        if not decision.allowed:
            return self._save_error(task, intent, server.id, target, decision.code or "POLICY_DISABLED", decision.message)

        rate_key = f"{task.id}:{server.id}:{target}"
        if not self.rate_limiter.allow(rate_key):
            return self._save_error(task, intent, server.id, target, "POLICY_DISABLED", "rate limit exceeded")

        mcp_tool_name = _resolve_mcp_tool_name(server, tool, args)
        if mcp_tool_name is None:
            return self._save_error(
                task,
                intent,
                server.id,
                target,
                "MCP_METHOD_REQUIRED",
                "an explicit registered MCP method is required",
            )
        method_spec = next((item for item in server.tools if item.name == mcp_tool_name), None)
        if method_spec is None:
            return self._save_error(
                task,
                intent,
                server.id,
                target,
                "MCP_METHOD_NOT_AVAILABLE",
                f"method {mcp_tool_name} is not exposed by {server.id}",
            )
        mcp_arguments, volumes = _prepare_mcp_arguments(server, target, args, mcp_tool_name)
        validation_error = _validate_mcp_arguments(method_spec.input_schema, mcp_arguments)
        if validation_error:
            return self._save_error(task, intent, server.id, target, "INVALID_TOOL_ARGUMENTS", validation_error)
        started_at = _utc_now()
        call_kwargs = {
            "server": server,
            "tool_name": mcp_tool_name,
            "arguments": mcp_arguments,
            "volumes": volumes,
            "timeout_seconds": int(args.get("timeout_seconds", 120)),
        }
        try:
            result = self.mcp_client.call_tool(**call_kwargs, max_output_bytes=max_output_bytes)
        except TypeError as exc:
            # Third-party test/dry-run clients predating the bounded-output
            # argument remain supported; real MCPClient always receives it.
            if "max_output_bytes" not in str(exc):
                raise
            result = self.mcp_client.call_tool(**call_kwargs)
        finished_at = _utc_now()
        payload = {
            "task_id": task.id,
            "intent_id": intent.id,
            "tool": server.id,
            "mcp_tool": mcp_tool_name,
            "target": target,
            "command": result.command,
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": result.returncode,
            "status": "timeout" if result.timed_out else "ok" if result.ok else "failed",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "output_truncated": getattr(result, "output_truncated", False),
        }
        return self.artifact_store.save_text(
            task_id=task.id,
            intent_id=intent.id,
            kind="tool_output",
            text=json.dumps(payload, ensure_ascii=False, indent=2),
            tool=server.id,
            target=target,
            suffix=".json",
        )

    def _save_error(
        self,
        task: TGATask,
        intent: Intent,
        tool: str,
        target: str,
        code: str,
        message: str,
    ) -> ArtifactRecord:
        payload = {
            "task_id": task.id,
            "intent_id": intent.id,
            "tool": tool,
            "target": target,
            "status": "failed",
            "error": {"code": code, "message": message, "retryable": False},
            "created_at": _utc_now(),
        }
        return self.artifact_store.save_text(
            task_id=task.id,
            intent_id=intent.id,
            kind="tool_output",
            text=json.dumps(payload, ensure_ascii=False, indent=2),
            tool=tool,
            target=target,
            suffix=".json",
        )


def _resolve_mcp_tool_name(server: MCPServerSpec, requested_tool: str, args: dict[str, Any]) -> str | None:
    explicit = args.get("mcp_tool") or args.get("tool_name")
    if explicit:
        return str(explicit)
    requested = requested_tool.lower().replace("-", "_")
    for tool in server.tools:
        if tool.name.lower() == requested or tool.name.lower().replace("_", "-") == requested_tool.lower():
            return tool.name
    # Never guess the first tool exposed by an MCP server.  The caller must
    # explicitly select a documented method when the server name is ambiguous.
    return None


def _validate_mcp_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Small JSON-schema guard for catalogued MCP methods.

    Full server-side validation still runs in MCP. This preflight prevents a
    model from invoking an unknown field/missing required argument blindly.
    """
    if not schema:
        return None
    required = schema.get("required", [])
    if isinstance(required, list):
        missing = [str(item) for item in required if item not in arguments]
        if missing:
            return f"missing required tool arguments: {', '.join(missing)}"
    properties = schema.get("properties")
    if isinstance(properties, dict) and schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            return f"unknown tool arguments: {', '.join(unknown)}"
    return None


def _arguments_without_runner_keys(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if key not in {"mcp_tool", "tool_name", "timeout_seconds"}}


LOCAL_CONTAINER_PATHS = {
    "binwalk-mcp": "/app/samples",
    "capa-mcp": "/app/samples",
    "gitleaks-mcp": "/target",
    "mcp-scan": "/app/target",
    "semgrep-mcp": "/code",
    "trivy-mcp": "/app/target",
    "yara-mcp": "/app/samples",
}


def _prepare_mcp_arguments(
    server: MCPServerSpec,
    target: str,
    args: dict[str, Any],
    mcp_tool_name: str,
) -> tuple[dict[str, Any], list[str]]:
    arguments = _arguments_without_runner_keys(args)
    container_path = LOCAL_CONTAINER_PATHS.get(server.id)
    if not container_path or not _is_existing_local_path(target):
        return arguments, []

    host_path = str(Path(target).resolve())
    volumes = [f"{host_path}:{container_path}:ro"]
    if server.id == "gitleaks-mcp":
        if mcp_tool_name == "gitleaks_scan_repo":
            arguments.setdefault("repo_path", container_path)
        elif mcp_tool_name == "gitleaks_scan_dir":
            arguments.setdefault("dir_path", container_path)
    else:
        for key in ("target", "path", "repo_path", "dir_path", "file_path", "directory"):
            if key in arguments:
                arguments[key] = container_path
        arguments.setdefault("target", container_path)
    return arguments, volumes


def _is_existing_local_path(value: str) -> bool:
    try:
        return Path(value).expanduser().exists()
    except (OSError, RuntimeError):
        return False


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
