"""Controlled bridge from runtime actions to concrete capabilities.

This module deliberately has no knowledge of boards, flags, or event storage.
It turns one validated ``ActionSpec`` into one ``ActionResult`` and leaves
confirmation and persistence orchestration to the caller.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from tga.contracts import ActionResult, ActionSpec, Intent, TGAError, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.tools.rate_limit import RateLimiter
from tga.tools.tool_runner import ToolRunner

from .http import execute_http, extract_candidate_flags, semantic_fingerprint
from .registry import CapabilityRegistry, build_default_registry
from .schemas import ArtifactInspectArguments, HTTPRequestArguments, WorkspacePythonArguments, WorkspaceReadArguments, WorkspaceWriteArguments
from .serializers import redact_text
from .workspace import resolve_solver_path


class ExecutionBudget:
    """A second enforcement layer beneath the Manager's session budget."""

    def __init__(
        self,
        max_actions_per_solver: int = 32,
        max_fingerprint_retries: int = 3,
        *,
        http_requests_per_minute: int = 30,
        http_burst: int = 5,
        max_mcp_concurrency: int = 2,
        max_action_timeout_s: int = 120,
        max_output_bytes: int = 262_144,
    ) -> None:
        self.max_actions_per_solver = max_actions_per_solver
        self.max_fingerprint_retries = max_fingerprint_retries
        self.max_action_timeout_s = max_action_timeout_s
        self.max_output_bytes = max_output_bytes
        self.actions: defaultdict[tuple[str, str], int] = defaultdict(int)
        self.fingerprints: defaultdict[tuple[str, str], int] = defaultdict(int)
        self.http_limiter = RateLimiter(
            default_rate_per_second=http_requests_per_minute / 60,
            default_burst=http_burst,
        )
        self._mcp_slots = threading.BoundedSemaphore(max_mcp_concurrency)
        self._mcp_acquired: set[str] = set()
        self._lock = threading.Lock()

    def reserve(
        self, action: ActionSpec, fingerprint: str | None = None, *, http_target: str | None = None
    ) -> TGAError | None:
        """Atomically reserve every quota required before process/network I/O."""
        with self._lock:
            key = (action.task_id, action.solver_id)
            if self.actions[key] >= self.max_actions_per_solver:
                return TGAError(code="ACTION_BUDGET_EXCEEDED", message="solver action budget exhausted")
            if fingerprint and self.fingerprints[(action.task_id, fingerprint)] >= self.max_fingerprint_retries:
                return TGAError(code="ACTION_BUDGET_EXCEEDED", message="semantic action retry budget exhausted")
            if action.capability == "http.request":
                # HTTP actions may use an in-scope absolute ``arguments.url``;
                # rate-limit the host actually requested rather than the
                # broader action target used for orchestration.
                host = _budget_host(http_target or action.target)
                if not host or not self.http_limiter.allow(f"{action.task_id}:{host}"):
                    return TGAError(code="ACTION_BUDGET_EXCEEDED", message=f"HTTP request rate budget exhausted for host {host or 'unknown'}")
            if action.capability == "tool.invoke" and not self._mcp_slots.acquire(blocking=False):
                return TGAError(code="ACTION_BUDGET_EXCEEDED", message="MCP concurrency budget exhausted", retryable=True)
            self.actions[key] += 1
            if fingerprint:
                self.fingerprints[(action.task_id, fingerprint)] += 1
            if action.capability == "tool.invoke":
                self._mcp_acquired.add(action.id)
        return None

    def release(self, action: ActionSpec) -> None:
        with self._lock:
            if action.id in self._mcp_acquired:
                self._mcp_acquired.remove(action.id)
                self._mcp_slots.release()


class ControlledActionExecutor:
    """Execute only registered HTTP and explicitly catalogued MCP actions."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        registry: CapabilityRegistry | None = None,
        tool_runner: ToolRunner | None = None,
        budget: ExecutionBudget | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.registry = registry or build_default_registry()
        self.tool_runner = tool_runner
        self.budget = budget or ExecutionBudget()

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
        """Return a structured outcome; never update a board or confirm a flag."""
        if action.task_id != task.id:
            return self._reject(action, "ACTION_TASK_MISMATCH", "action task_id does not match the execution task")

        registered = self.registry.get(action.capability)
        if registered is None:
            return self._reject(action, "UNKNOWN_CAPABILITY", f"capability is not registered: {action.capability}")
        if registered.spec.kind != action.kind:
            return self._reject(
                action,
                "CAPABILITY_KIND_MISMATCH",
                f"{action.capability} requires kind={registered.spec.kind}",
            )
        if task.mode not in registered.spec.modes:
            return self._reject(action, "CAPABILITY_MODE_NOT_ALLOWED", f"{action.capability} is unavailable for {task.mode}")
        try:
            arguments = self.registry.validate(action.capability, action.arguments)
        except (ValidationError, ValueError) as exc:
            return self._reject(action, "INVALID_ACTION_ARGUMENTS", redact_text(str(exc), 500))

        if _risk_rank(action.risk) < _risk_rank(registered.spec.risk):
            return self._reject(action, "RISK_UNDERSPECIFIED", "action risk is lower than capability risk")
        if action.hypothesis_id is None and not (action.capability in {"http.request", "workspace.read", "artifact.inspect"} and action.risk == "passive"):
            return self._reject(action, "HYPOTHESIS_REQUIRED", "active execution requires a hypothesis_id")
        fingerprint = None
        http_target = None
        if isinstance(arguments, HTTPRequestArguments):
            try:
                from .http import _resolve_url
                http_target = _resolve_url(action.target, arguments)
                fingerprint = semantic_fingerprint(action=action, args=arguments, url=http_target)
            except ValueError:
                pass
        budget_error = self.budget.reserve(action, fingerprint, http_target=http_target)
        if budget_error:
            return self._reject(action, budget_error.code, budget_error.message)

        try:
            if action.capability == "http.request":
                return self._execute_http(task=task, action=action, arguments=arguments)
            if action.capability == "tool.invoke":
                return self._execute_tool(task=task, action=action, arguments=arguments)
            if isinstance(arguments, WorkspaceReadArguments):
                return self._workspace_read(task=task, action=action, arguments=arguments, workspace=workspace)
            if isinstance(arguments, WorkspaceWriteArguments):
                return self._workspace_write(task=task, action=action, arguments=arguments, workspace=workspace)
            if isinstance(arguments, WorkspacePythonArguments):
                return self._workspace_python(task=task, action=action, arguments=arguments, workspace=workspace)
            if isinstance(arguments, ArtifactInspectArguments):
                return self._artifact_inspect(task=task, action=action, arguments=arguments)
            return self._reject(
                action,
                "CAPABILITY_NOT_IMPLEMENTED",
                f"{action.capability} is registered but not enabled by this executor",
            )
        finally:
            self.budget.release(action)

    def _execute_http(self, *, task: TGATask, action: ActionSpec, arguments: Any) -> ActionResult:
        try:
            # The action target is part of A's approved request.  Preserve the
            # task scope while ensuring relative paths resolve against it.
            execution_task = task.model_copy(update={"target": action.target})
            bounded_args = arguments.model_copy(update={"timeout": min(arguments.timeout, self.budget.max_action_timeout_s)})
            payload, raw, facts, leads = execute_http(
                task=execution_task, action=action, args=bounded_args, max_output_bytes=self.budget.max_output_bytes
            )
            artifact = self.artifact_store.save_text(
                task_id=task.id,
                intent_id=action.hypothesis_id,
                kind="http_response",
                text=json.dumps(payload, ensure_ascii=False, indent=2),
                tool="http.request",
                target=payload["final_url"],
                suffix=".json",
            )
            artifact_ids = [artifact.id]
            if payload["truncated"]:
                blob = self.artifact_store.save_bytes(
                    task_id=task.id,
                    intent_id=action.hypothesis_id,
                    kind="file",
                    data=raw,
                    tool="http.request",
                    target=payload["final_url"],
                    suffix=".body",
                )
                artifact_ids.append(blob.id)
            candidates = extract_candidate_flags(raw, task.flag_format)
            status = "succeeded" if not payload.get("error") else "failed"
            error = None if status == "succeeded" else TGAError(code="HTTP_REQUEST_FAILED", message=str(payload["error"]), retryable=True)
            return ActionResult(
                action_id=action.id,
                task_id=task.id,
                solver_id=action.solver_id,
                status=status,
                summary=_http_summary(payload),
                artifact_ids=artifact_ids,
                facts=facts,
                leads=leads,
                candidate_flags=candidates,
                error=error,
            )
        except PermissionError as exc:
            return self._reject(action, str(exc) or "ACTION_NOT_ALLOWED", "HTTP action was rejected by scope or risk policy")
        except (ValueError, RuntimeError) as exc:
            return self._reject(action, "HTTP_EXECUTION_FAILED", redact_text(str(exc), 500), retryable=True)

    def _execute_tool(self, *, task: TGATask, action: ActionSpec, arguments: Any) -> ActionResult:
        if self.tool_runner is None:
            return self._reject(action, "TOOL_RUNNER_UNAVAILABLE", "MCP tool execution is not configured", retryable=True)

        server = self.tool_runner.catalog.get(arguments.tool_id)
        if server is None:
            return self._reject(action, "TOOL_NOT_AVAILABLE", f"tool is not registered: {arguments.tool_id}")
        if not any(item.name == arguments.tool_method for item in server.tools):
            return self._reject(
                action,
                "UNKNOWN_TOOL_METHOD",
                f"tool method is not registered for {server.id}: {arguments.tool_method}",
            )

        intent = Intent(
            id=f"action_{action.id}",
            task_id=task.id,
            kind="verify",
            target=action.target,
            goal=action.rationale,
            risk=action.risk,
        )
        try:
            artifact = self.tool_runner.run_tool(
                task=task,
                intent=intent,
                tool=server.id,
                target=action.target,
                args={
                    "mcp_tool": arguments.tool_method,
                    "timeout_seconds": min(arguments.timeout, self.budget.max_action_timeout_s),
                    **arguments.arguments,
                },
                max_output_bytes=self.budget.max_output_bytes,
            )
            payload = _json_payload(self.artifact_store.read_text(artifact.id))
            error_payload = payload.get("error") if isinstance(payload, dict) else None
            error = _error_from_payload(error_payload)
            status_value = str(payload.get("status") or "failed") if isinstance(payload, dict) else "failed"
            if status_value != "ok" and error is None:
                error = TGAError(
                    code="TOOL_TIMEOUT" if status_value == "timeout" else "TOOL_EXECUTION_FAILED",
                    message=f"{server.id}.{arguments.tool_method} returned status {status_value}",
                    retryable=status_value == "timeout",
                )
            status = "succeeded" if status_value == "ok" and error is None else "failed"
            output = "\n".join(str(payload.get(key) or "") for key in ("stdout", "stderr")) if isinstance(payload, dict) else ""
            return ActionResult(
                action_id=action.id,
                task_id=task.id,
                solver_id=action.solver_id,
                status=status,
                summary=_tool_summary(server.id, arguments.tool_method, status_value, error),
                artifact_ids=[artifact.id],
                facts=[f"{server.id}.{arguments.tool_method} -> {status_value}"],
                candidate_flags=_candidate_flags(output, task.flag_format),
                error=error,
            )
        except Exception as exc:  # Existing MCP clients can fail at process boundaries.
            return self._reject(action, "TOOL_EXECUTION_FAILED", redact_text(str(exc), 500), retryable=True)

    def _workspace_read(self, *, task: TGATask, action: ActionSpec, arguments: WorkspaceReadArguments, workspace: Path) -> ActionResult:
        try:
            path = resolve_solver_path(workspace, arguments.relative_path)
            size = path.stat().st_size
            with path.open("rb") as source:
                source.seek(arguments.offset)
                raw = source.read(min(arguments.limit, self.budget.max_output_bytes))
            excerpt = raw.decode("utf-8", errors="replace")
        except PermissionError as exc:
            return self._reject(action, str(exc), "workspace path escapes the solver workspace")
        except OSError as exc:
            return self._reject(action, "WORKSPACE_READ_FAILED", redact_text(str(exc), 500))
        artifact = self.artifact_store.save_text(task_id=task.id, intent_id=action.hypothesis_id, kind="file", text=json.dumps({"relative_path": arguments.relative_path, "offset": arguments.offset, "size": size, "excerpt": redact_text(excerpt, self.budget.max_output_bytes), "truncated": arguments.offset + len(raw) < size}, ensure_ascii=False), tool="workspace.read", target=str(path), suffix=".json")
        return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded", summary=f"read {arguments.relative_path} ({size} bytes)", artifact_ids=[artifact.id], facts=[f"workspace file observed: {arguments.relative_path}"])

    def _workspace_write(self, *, task: TGATask, action: ActionSpec, arguments: WorkspaceWriteArguments, workspace: Path) -> ActionResult:
        try:
            path = resolve_solver_path(workspace, arguments.relative_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments.content, encoding="utf-8")
        except PermissionError as exc:
            return self._reject(action, str(exc), "workspace path escapes the solver workspace")
        except OSError as exc:
            return self._reject(action, "WORKSPACE_WRITE_FAILED", redact_text(str(exc), 500))
        artifact = self.artifact_store.save_text(task_id=task.id, intent_id=action.hypothesis_id, kind="file", text=json.dumps({"relative_path": arguments.relative_path, "bytes_written": len(arguments.content.encode())}), tool="workspace.write", target=str(path), suffix=".json")
        return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded", summary=f"wrote {arguments.relative_path}", artifact_ids=[artifact.id])

    def _workspace_python(self, *, task: TGATask, action: ActionSpec, arguments: WorkspacePythonArguments, workspace: Path) -> ActionResult:
        root = workspace.resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            if arguments.source is not None:
                script = root / f".tga_{action.id}.py"
                script.write_text(arguments.source, encoding="utf-8")
            else:
                script = resolve_solver_path(root, arguments.script_path or "")
            returncode, stdout, stderr, timed_out, output_truncated = _run_bounded_python(
                script=script,
                argv=arguments.argv,
                cwd=root,
                timeout=min(arguments.timeout, self.budget.max_action_timeout_s),
                output_limit=self.budget.max_output_bytes,
            )
        except PermissionError as exc:
            return self._reject(action, str(exc), "workspace path escapes the solver workspace")
        except OSError as exc:
            return self._reject(action, "WORKSPACE_PYTHON_FAILED", redact_text(str(exc), 500))
        output_limit = self.budget.max_output_bytes
        payload = {"script": script.relative_to(root).as_posix(), "argv": arguments.argv, "timeout": min(arguments.timeout, self.budget.max_action_timeout_s), "timed_out": timed_out, "exit_code": None if timed_out else returncode, "stdout": redact_text(stdout, output_limit), "stderr": redact_text(stderr, output_limit), "truncated": output_truncated}
        artifact = self.artifact_store.save_text(task_id=task.id, intent_id=action.hypothesis_id, kind="tool_output", text=json.dumps(payload, ensure_ascii=False), tool="workspace.python", target=str(script), suffix=".json")
        if timed_out:
            return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="failed", summary="workspace Python timed out", artifact_ids=[artifact.id], error=TGAError(code="ACTION_TIMEOUT", message="workspace Python timed out"))
        return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded" if returncode == 0 else "failed", summary=f"workspace Python exited {returncode}", artifact_ids=[artifact.id], candidate_flags=_candidate_flags(stdout + "\n" + stderr, task.flag_format))

    def _artifact_inspect(self, *, task: TGATask, action: ActionSpec, arguments: ArtifactInspectArguments) -> ActionResult:
        text = self.artifact_store.read_text(arguments.artifact_id)
        if not text:
            return self._reject(action, "ARTIFACT_NOT_FOUND", "artifact does not exist")
        excerpt = text[arguments.offset : arguments.offset + min(arguments.limit, self.budget.max_output_bytes)]
        if arguments.query:
            location = excerpt.lower().find(arguments.query.lower())
            excerpt = excerpt[max(0, location - 500) : location + len(arguments.query) + 1500] if location >= 0 else ""
        artifact = self.artifact_store.save_text(task_id=task.id, intent_id=action.hypothesis_id, kind="file", text=json.dumps({"source_artifact_id": arguments.artifact_id, "offset": arguments.offset, "excerpt": redact_text(excerpt, arguments.limit)}, ensure_ascii=False), tool="artifact.inspect", suffix=".json")
        return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded", summary=f"inspected {arguments.artifact_id}", artifact_ids=[artifact.id], facts=["artifact excerpt loaded"])

    def _reject(self, action: ActionSpec, code: str, message: str, *, retryable: bool = False) -> ActionResult:
        error = TGAError(code=code, message=message, retryable=retryable)
        artifact = self.artifact_store.save_text(
            task_id=action.task_id,
            intent_id=action.hypothesis_id,
            kind="tool_output",
            text=json.dumps(
                {"action_id": action.id, "capability": action.capability, "status": "blocked", "error": error.model_dump()},
                ensure_ascii=False,
            ),
            tool=action.capability,
            target=action.target,
            suffix=".json",
        )
        return ActionResult(
            action_id=action.id,
            task_id=action.task_id,
            solver_id=action.solver_id,
            status="blocked",
            summary=message,
            artifact_ids=[artifact.id],
            error=error,
        )


def _http_summary(payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return f"HTTP request failed for {payload.get('final_url')}: {payload['error']}"
    return f"HTTP {payload.get('status')} from {payload.get('final_url')} ({payload.get('duration_ms')} ms)"


def _tool_summary(tool_id: str, method: str, status: str, error: TGAError | None) -> str:
    if error:
        return f"{tool_id}.{method} failed: {error.message}"
    return f"{tool_id}.{method} completed with status {status}"


def _json_payload(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_from_payload(payload: Any) -> TGAError | None:
    if not isinstance(payload, dict):
        return None
    return TGAError(
        code=str(payload.get("code") or "TOOL_EXECUTION_FAILED"),
        message=str(payload.get("message") or "tool execution failed"),
        retryable=bool(payload.get("retryable")),
    )


def _candidate_flags(text: str, flag_format: str | None) -> list[str]:
    try:
        pattern = re.compile(flag_format or r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}")
    except re.error:
        pattern = re.compile(r"flag\{[^}\s]{4,200}\}")
    return list(dict.fromkeys(match.group(0) for match in pattern.finditer(text)))


def _risk_rank(value: str) -> int:
    return {"passive": 0, "active": 1, "destructive": 2}.get(value, -1)


def _budget_host(target: str) -> str:
    parsed = urlparse(target if "://" in target else f"//{target}")
    if not parsed.hostname:
        return ""
    return f"{parsed.hostname.lower()}:{parsed.port}" if parsed.port else parsed.hostname.lower()


def _run_bounded_python(
    *, script: Path, argv: list[str], cwd: Path, timeout: int, output_limit: int
) -> tuple[int, str, str, bool, bool]:
    """Drain both pipes while retaining at most ``output_limit`` bytes each."""
    process = subprocess.Popen(
        [sys.executable, "-I", str(script), *argv],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    captured: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}

    def drain(name: str, stream: Any) -> None:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            remaining = output_limit - len(captured[name])
            if remaining > 0:
                captured[name].extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated[name] = True

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        returncode = process.wait(timeout=5)
    for reader in readers:
        reader.join(timeout=2)
    return (
        returncode,
        bytes(captured["stdout"]).decode("utf-8", errors="replace"),
        bytes(captured["stderr"]).decode("utf-8", errors="replace"),
        timed_out,
        truncated["stdout"] or truncated["stderr"],
    )
