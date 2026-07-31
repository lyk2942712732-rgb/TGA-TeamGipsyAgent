"""Controlled bridge from runtime actions to concrete capabilities.

This module deliberately has no knowledge of strategy state, flags, or event storage.
It turns one validated ``ActionSpec`` into one ``ActionResult`` and leaves
confirmation and persistence orchestration to the caller.
"""

from __future__ import annotations

import json
import html
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

from tga.contracts import ActionResult, ActionSpec, TGAError, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.indexing import build_artifact_index, retrieve_segments
from tga.tools.rate_limit import RateLimiter
from tga.tools.tool_policy import is_allowed

from .http import execute_http, extract_candidate_flags, semantic_fingerprint
from .http_session import HTTPSessionRegistry
from .registry import CapabilityRegistry, build_default_registry
from .schemas import ArtifactInspectArguments, HTTPRequestArguments, WorkspacePythonArguments, WorkspaceReadArguments, WorkspaceShellArguments, WorkspaceWriteArguments
from .serializers import redact_text
from .workspace import resolve_solver_path


class ExecutionBudget:
    """Runtime resource controls that do not decide challenge completion.

    BreachWeave-style solver sessions are long lived.  Action and semantic
    counters are therefore telemetry by default, not hidden terminal gates.
    Callers may still opt into finite limits for a dedicated batch job.
    """

    def __init__(
        self,
        max_actions_per_solver: int | None = None,
        max_fingerprint_retries: int | None = None,
        *,
        http_requests_per_minute: int = 30,
        http_concurrency: int = 2,
        process_concurrency: int = 2,
        http_burst: int = 5,
        max_action_timeout_s: int = 120,
        http_timeout_s: int | None = None,
        process_timeout_s: int | None = None,
        max_output_bytes: int = 262_144,
        unrestricted: bool = False,
    ) -> None:
        self.max_actions_per_solver = max_actions_per_solver
        self.max_fingerprint_retries = max_fingerprint_retries
        self.max_action_timeout_s = max_action_timeout_s
        self.http_timeout_s = http_timeout_s or max_action_timeout_s
        self.process_timeout_s = process_timeout_s or max_action_timeout_s
        self.max_output_bytes = max_output_bytes
        self.unrestricted = unrestricted
        self.http_concurrency = max(1, http_concurrency)
        self.process_concurrency = max(1, process_concurrency)
        self.actions: defaultdict[tuple[str, str], int] = defaultdict(int)
        self.fingerprints: defaultdict[tuple[str, str], int] = defaultdict(int)
        self.http_limiter = RateLimiter(
            default_rate_per_second=http_requests_per_minute / 60,
            default_burst=http_burst,
        )
        self._lock = threading.Lock()
        self._active_http: defaultdict[str, int] = defaultdict(int)
        self._active_process: defaultdict[str, int] = defaultdict(int)

    def reserve(
        self, action: ActionSpec, fingerprint: str | None = None, *, http_target: str | None = None
    ) -> TGAError | None:
        """Atomically reserve every quota required before process/network I/O."""
        with self._lock:
            key = (action.task_id, action.solver_id)
            if self.unrestricted:
                self.actions[key] += 1
                if fingerprint:
                    self.fingerprints[(action.task_id, fingerprint)] += 1
                return None
            if self.max_actions_per_solver is not None and self.actions[key] >= self.max_actions_per_solver:
                return TGAError(code="ACTION_BUDGET_EXCEEDED", message="solver action budget exhausted")
            if (
                fingerprint
                and self.max_fingerprint_retries is not None
                and self.fingerprints[(action.task_id, fingerprint)] >= self.max_fingerprint_retries
            ):
                return TGAError(code="ACTION_BUDGET_EXCEEDED", message="semantic action retry budget exhausted")
            if action.capability == "http.request":
                # HTTP actions may use an in-scope absolute ``arguments.url``;
                # rate-limit the host actually requested rather than the
                # broader action target used for orchestration.
                host = _budget_host(http_target or action.target)
                if not host or not self.http_limiter.allow(f"{action.task_id}:{host}"):
                    return TGAError(code="RATE_LIMITED", message=f"HTTP request rate limit reached for host {host or 'unknown'}", retryable=True)
                if self._active_http[action.task_id] >= self.http_concurrency:
                    return TGAError(code="CONCURRENCY_WAIT", message="HTTP concurrency limit reached", retryable=True)
                self._active_http[action.task_id] += 1
            if action.capability in {"workspace.python", "workspace.shell"}:
                if self._active_process[action.task_id] >= self.process_concurrency:
                    return TGAError(code="CONCURRENCY_WAIT", message="local compute concurrency limit reached", retryable=True)
                self._active_process[action.task_id] += 1
            self.actions[key] += 1
            if fingerprint:
                self.fingerprints[(action.task_id, fingerprint)] += 1
        return None

    def release(self, action: ActionSpec) -> None:
        with self._lock:
            if action.capability == "http.request" and self._active_http[action.task_id] > 0:
                self._active_http[action.task_id] -= 1
            if action.capability in {"workspace.python", "workspace.shell"} and self._active_process[action.task_id] > 0:
                self._active_process[action.task_id] -= 1


class ControlledActionExecutor:
    """Execute only registered HTTP and explicitly catalogued MCP actions."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        registry: CapabilityRegistry | None = None,
        budget: ExecutionBudget | None = None,
        http_sessions: HTTPSessionRegistry | None = None,
        sandbox_manager: Any | None = None,
        fencing_token_provider: Any | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.registry = registry or build_default_registry()
        self.budget = budget or ExecutionBudget()
        self.http_sessions = http_sessions or HTTPSessionRegistry()
        self.sandbox_manager = sandbox_manager
        self.fencing_token_provider = fencing_token_provider or (lambda _solver_id: 1)

    def close_http_sessions(self, *, task_id: str, solver_id: str | None = None) -> int:
        return self.http_sessions.destroy(task_id=task_id, solver_id=solver_id)

    def http_session_snapshot(self, *, task_id: str, solver_id: str) -> dict:
        return self.http_sessions.snapshot(task_id=task_id, solver_id=solver_id)

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
        """Return a structured outcome; never mutate strategy state or confirm a flag."""
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
        fingerprint = None
        http_target = None
        if isinstance(arguments, HTTPRequestArguments):
            try:
                from .http import _resolve_url
                http_target = _resolve_url(task.task_entry_url or action.target, arguments)
                fingerprint = semantic_fingerprint(action=action, args=arguments, url=http_target)
            except ValueError:
                pass
        decision = is_allowed(
            tool=action.capability,
            target=http_target or action.actual_target or action.target,
            task=task,
            risk=action.risk,
            action=arguments.method if isinstance(arguments, HTTPRequestArguments) else action.capability,
            sandboxed=task.execution_policy.local_compute.mode == "isolated" if task.execution_policy else False,
            approved=action.authorization.get("approved_action_id") == action.id,
        )
        if not decision.allowed:
            return self._reject(action, decision.code or "POLICY_DENIED", decision.reason)
        budget_error = self.budget.reserve(action, fingerprint, http_target=http_target)
        if budget_error:
            return self._reject(action, budget_error.code, budget_error.message)

        try:
            if action.capability == "http.request":
                return self._execute_http(task=task, action=action, arguments=arguments)
            if isinstance(arguments, WorkspaceReadArguments):
                return self._workspace_read(task=task, action=action, arguments=arguments, workspace=workspace)
            if isinstance(arguments, WorkspaceWriteArguments):
                return self._workspace_write(task=task, action=action, arguments=arguments, workspace=workspace)
            if isinstance(arguments, WorkspacePythonArguments):
                return self._workspace_python(task=task, action=action, arguments=arguments, workspace=workspace)
            if isinstance(arguments, WorkspaceShellArguments):
                return self._workspace_shell(task=task, action=action, arguments=arguments, workspace=workspace)
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
            bounded_args = arguments.model_copy(update={"timeout": min(arguments.timeout, self.budget.http_timeout_s)})
            payload, raw, facts, leads = execute_http(
                task=task, action=action, args=bounded_args,
                max_output_bytes=self.budget.max_output_bytes, sessions=self.http_sessions,
            )
            body_artifact = None
            if raw:
                content_type = str(payload.get("content_type") or "")
                suffix = ".html" if "html" in content_type.casefold() else ".body"
                body_artifact = self.artifact_store.save_bytes(
                    task_id=task.id,
                    intent_id=action.strategy_step_id,
                    kind="http_body",
                    data=raw,
                    tool="http.request.body",
                    target=payload["final_url"],
                    suffix=suffix,
                )
                index = build_artifact_index(
                    task_id=task.id,
                    artifact_id=body_artifact.id,
                    raw=raw,
                    content_type=content_type,
                )
                payload["body_artifact_id"] = body_artifact.id
                payload["document"] = {
                    "extraction_status": index.extraction_status,
                    "document_type": index.document_type,
                    "summary": index.summary,
                    "segment_refs": [item.ref for item in index.segments[:12]],
                }
                # The complete body is immutable in body_artifact. Keep only a
                # readable, high-signal projection in the JSON response record.
                payload["body_excerpt"] = index.summary[: min(6000, self.budget.max_output_bytes)]
            artifact = self.artifact_store.save_text(
                task_id=task.id,
                intent_id=action.strategy_step_id,
                kind="http_response",
                text=json.dumps(payload, ensure_ascii=False, indent=2),
                tool="http.request",
                target=payload["final_url"],
                suffix=".json",
            )
            artifact_ids = [artifact.id]
            if body_artifact is not None:
                artifact_ids.append(body_artifact.id)
            candidates = extract_candidate_flags(raw, task.flag_format)
            availability = payload.get("challenge_availability")
            status = "succeeded" if not payload.get("error") and not availability else "failed"
            marker = payload.get("expected_marker") or {}
            if marker and not marker.get("found"):
                leads.append(f"expected marker not observed: {marker.get('value')}")
            if availability:
                error = TGAError(
                    code="CHALLENGE_UNAVAILABLE",
                    message=f"challenge endpoint reports {availability}",
                    retryable=availability == "provisioning",
                )
            else:
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
        artifact = self.artifact_store.save_text(task_id=task.id, intent_id=action.strategy_step_id, kind="file", text=json.dumps({"relative_path": arguments.relative_path, "offset": arguments.offset, "size": size, "excerpt": redact_text(excerpt, self.budget.max_output_bytes), "truncated": arguments.offset + len(raw) < size}, ensure_ascii=False), tool="workspace.read", target=str(path), suffix=".json")
        return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded", summary=f"read {arguments.relative_path} ({size} bytes)", artifact_ids=[artifact.id], facts=[f"workspace file observed: {arguments.relative_path}"], candidate_flags=_candidate_flags(excerpt, task.flag_format))

    def _workspace_write(self, *, task: TGATask, action: ActionSpec, arguments: WorkspaceWriteArguments, workspace: Path) -> ActionResult:
        try:
            path = resolve_solver_path(workspace, arguments.relative_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments.content, encoding="utf-8")
        except PermissionError as exc:
            return self._reject(action, str(exc), "workspace path escapes the solver workspace")
        except OSError as exc:
            return self._reject(action, "WORKSPACE_WRITE_FAILED", redact_text(str(exc), 500))
        artifact = self.artifact_store.save_text(task_id=task.id, intent_id=action.strategy_step_id, kind="file", text=json.dumps({"relative_path": arguments.relative_path, "bytes_written": len(arguments.content.encode())}), tool="workspace.write", target=str(path), suffix=".json")
        return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded", summary=f"wrote {arguments.relative_path}", artifact_ids=[artifact.id])

    def _workspace_python(self, *, task: TGATask, action: ActionSpec, arguments: WorkspacePythonArguments, workspace: Path) -> ActionResult:
        root = workspace.resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            if arguments.source is not None:
                script = root / "work" / f".tga_{action.id}.py"
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text(arguments.source, encoding="utf-8")
            else:
                script = resolve_solver_path(root, arguments.script_path or "")
            container_script = _container_workspace_path(root, script)
            returncode, stdout, stderr, timed_out, output_truncated = _run_isolated_process(
                workspace=root,
                command=["python", container_script, *arguments.argv],
                argv=arguments.argv,
                timeout=min(arguments.timeout, self.budget.process_timeout_s),
                output_limit=self.budget.max_output_bytes,
                sandbox_manager=self.sandbox_manager,
                task_id=task.id,
                solver_id=action.solver_id,
                fencing_token=self.fencing_token_provider(action.solver_id),
                action_id=action.id,
            )
        except PermissionError as exc:
            return self._reject(action, str(exc), "workspace path escapes the solver workspace")
        except OSError as exc:
            if "ISOLATED_RUNTIME_UNAVAILABLE" in str(exc):
                return self._reject(action, "ISOLATED_RUNTIME_UNAVAILABLE", redact_text(str(exc), 500), retryable=True)
            return self._reject(action, "WORKSPACE_PYTHON_FAILED", redact_text(str(exc), 500))
        output_limit = self.budget.max_output_bytes
        payload = {"script": script.relative_to(root).as_posix(), "argv": arguments.argv, "timeout": min(arguments.timeout, self.budget.process_timeout_s), "timed_out": timed_out, "exit_code": None if timed_out else returncode, "stdout": redact_text(stdout, output_limit), "stderr": redact_text(stderr, output_limit), "truncated": output_truncated}
        artifact = self.artifact_store.save_text(task_id=task.id, intent_id=action.strategy_step_id, kind="tool_output", text=json.dumps(payload, ensure_ascii=False), tool="workspace.python", target=str(script), suffix=".json")
        if timed_out:
            return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="failed", summary="workspace Python timed out", artifact_ids=[artifact.id], error=TGAError(code="ACTION_TIMEOUT", message="workspace Python timed out"))
        return ActionResult(action_id=action.id, task_id=task.id, solver_id=action.solver_id, status="succeeded" if returncode == 0 else "failed", summary=f"workspace Python exited {returncode}", artifact_ids=[artifact.id], candidate_flags=_candidate_flags(stdout + "\n" + stderr, task.flag_format))

    def _workspace_shell(self, *, task: TGATask, action: ActionSpec, arguments: WorkspaceShellArguments, workspace: Path) -> ActionResult:
        root = workspace.resolve()
        root.mkdir(parents=True, exist_ok=True)
        timeout = min(arguments.timeout, self.budget.process_timeout_s)
        try:
            returncode, stdout, stderr, timed_out, output_truncated = _run_isolated_process(
                workspace=root, command=["/bin/sh", "-lc", arguments.command], argv=[],
                timeout=timeout, output_limit=self.budget.max_output_bytes,
                sandbox_manager=self.sandbox_manager,
                task_id=task.id,
                solver_id=action.solver_id,
                fencing_token=self.fencing_token_provider(action.solver_id),
                action_id=action.id,
            )
        except OSError as exc:
            if "ISOLATED_RUNTIME_UNAVAILABLE" in str(exc):
                return self._reject(action, "ISOLATED_RUNTIME_UNAVAILABLE", redact_text(str(exc), 500), retryable=True)
            return self._reject(action, "WORKSPACE_SHELL_FAILED", redact_text(str(exc), 500))
        payload = {
            "command": arguments.command,
            "timeout": timeout,
            "timed_out": timed_out,
            "exit_code": None if timed_out else returncode,
            "stdout": redact_text(stdout, self.budget.max_output_bytes),
            "stderr": redact_text(stderr, self.budget.max_output_bytes),
            "truncated": output_truncated,
        }
        artifact = self.artifact_store.save_text(
            task_id=task.id, intent_id=action.strategy_step_id, kind="tool_output",
            text=json.dumps(payload, ensure_ascii=False), tool="workspace.shell",
            target=str(root), suffix=".json",
        )
        output = stdout + "\n" + stderr
        if timed_out:
            return ActionResult(
                action_id=action.id, task_id=task.id, solver_id=action.solver_id,
                status="failed", summary="workspace shell timed out", artifact_ids=[artifact.id],
                candidate_flags=_candidate_flags(output, task.flag_format),
                error=TGAError(code="ACTION_TIMEOUT", message="workspace shell timed out", retryable=True),
            )
        return ActionResult(
            action_id=action.id, task_id=task.id, solver_id=action.solver_id,
            status="succeeded" if returncode == 0 else "failed",
            summary=f"workspace shell exited {returncode}", artifact_ids=[artifact.id],
            leads=[redact_text(output, 12_000)] if output.strip() else [],
            candidate_flags=_candidate_flags(output, task.flag_format),
        )

    def _artifact_inspect(self, *, task: TGATask, action: ActionSpec, arguments: ArtifactInspectArguments) -> ActionResult:
        text = self.artifact_store.read_text(arguments.artifact_id)
        if not text:
            return self._reject(action, "ARTIFACT_NOT_FOUND", "artifact does not exist")
        index = build_artifact_index(
            task_id=task.id,
            artifact_id=arguments.artifact_id,
            raw=text.encode("utf-8", errors="replace"),
        )
        retrieval = retrieve_segments(
            index,
            query=arguments.query,
            section=arguments.section,
            offset=arguments.offset,
            limit=min(arguments.limit, self.budget.max_output_bytes, 12_000),
        )
        excerpt = "\n\n".join(
            f"[{item['ref']}] {item['heading']}\n{item['text']}" for item in retrieval["matches"]
        )
        matched = bool(retrieval["matches"]) if arguments.query or arguments.section else True
        # Inspection is a read view over an existing immutable artifact.  Do
        # not create artifact-of-artifact chains: they drown useful HTTP/tool
        # output, inflate the graph and make recovery context progressively
        # worse.  Reusing the source id preserves provenance exactly.
        return ActionResult(
            action_id=action.id,
            task_id=task.id,
            solver_id=action.solver_id,
            status="succeeded",
            summary=f"retrieved {arguments.artifact_id}" + (" (query matched)" if matched else " (query not found)"),
            artifact_ids=[arguments.artifact_id],
            leads=[redact_text(excerpt, min(arguments.limit, 12_000))] if excerpt else [],
            candidate_flags=_candidate_flags(excerpt, task.flag_format),
        )

    def _reject(self, action: ActionSpec, code: str, message: str, *, retryable: bool = False) -> ActionResult:
        error = TGAError(code=code, message=message, retryable=retryable)
        artifact = self.artifact_store.save_text(
            task_id=action.task_id,
            intent_id=action.strategy_step_id,
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


def _artifact_search_text(text: str) -> str:
    """Flatten common executor envelopes into readable tool output."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return html.unescape(text)
    if not isinstance(payload, dict):
        return html.unescape(text)
    values: list[str] = []
    for key in ("body_excerpt", "excerpt", "stdout", "stderr", "summary", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            values.append(value)
        elif isinstance(value, dict):
            values.append(json.dumps(value, ensure_ascii=False))
    page = payload.get("page")
    if isinstance(page, dict):
        values.append(json.dumps(page, ensure_ascii=False))
    return html.unescape("\n".join(values) if values else text)


def _query_excerpt(text: str, query: str | None) -> str:
    if not query:
        return text
    # Model queries are often descriptive phrases, while tool output contains
    # only a subset of those words.  Match useful tokens independently and
    # merge bounded windows instead of requiring the whole phrase verbatim.
    tokens = list(dict.fromkeys(re.findall(r"[A-Za-z0-9_./:-]{3,}", query.casefold())))
    lowered = text.casefold()
    locations = sorted({location for token in tokens if (location := lowered.find(token)) >= 0})
    if not locations:
        return ""
    chunks: list[str] = []
    for location in locations[:8]:
        chunks.append(text[max(0, location - 700) : location + 2200])
    return "\n...\n".join(chunks)


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
    return _run_bounded_process(
        command=[sys.executable, "-I", str(script), *argv],
        cwd=cwd,
        timeout=timeout,
        output_limit=output_limit,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _run_isolated_process(
    *, workspace: Path, command: list[str], argv: list[str], timeout: int, output_limit: int,
    sandbox_manager: Any | None = None, task_id: str = "", solver_id: str = "",
    fencing_token: int = 1, action_id: str = "",
) -> tuple[int, str, str, bool, bool]:
    """Run local compute only inside a locked-down, network-isolated Docker worker.

    ``network_inheritance=task_network_policy`` means network use must be
    mediated by the host ``http.request`` capability. Direct container
    networking stays disabled so compute cannot bypass DNS pinning, SSRF
    checks, redirect authorization, or per-task rate limits.
    """
    del argv
    if sandbox_manager is not None and sandbox_manager.config.runtime == "enforced":
        from tga.sandbox.models import ProcessSpec

        handle = sandbox_manager.acquire(
            task_id=task_id,
            solver_id=solver_id,
            profile_id="offline-analysis",
            fencing_token=fencing_token,
            idempotency_key=action_id,
        )
        sandbox_command = [
            value.removeprefix("/workspace/")
            if value.startswith("/workspace/")
            else value
            for value in command
        ]
        frames, result = sandbox_manager.exec(
            handle,
            ProcessSpec(
                argv=tuple(sandbox_command),
                logical_workspace="solver",
                timeout_seconds=timeout,
            ),
        )
        # Consume the iterator even when a provider also returns bounded
        # aggregate output; streaming providers may populate only frames.
        frame_values = tuple(frames)
        stdout = result.stdout or b"".join(
            frame.data for frame in frame_values if frame.stream == "stdout"
        )
        stderr = result.stderr or b"".join(
            frame.data for frame in frame_values if frame.stream == "stderr"
        )
        return (
            result.exit_code if result.exit_code is not None else -1,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            result.timed_out,
            result.truncated,
        )
    root = workspace.resolve()
    inputs = root / "inputs"
    work = root / "work"
    artifacts = root / "artifacts"
    for path in (inputs, work, artifacts):
        path.mkdir(parents=True, exist_ok=True)
    image = os.environ.get("TGA_ISOLATED_COMPUTE_IMAGE", "python:3.12-alpine").strip()
    if not image:
        raise OSError("ISOLATED_RUNTIME_UNAVAILABLE: no isolated compute image configured")
    try:
        probe = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise OSError("ISOLATED_RUNTIME_UNAVAILABLE: Docker runtime is unavailable") from exc
    if probe.returncode != 0:
        raise OSError("ISOLATED_RUNTIME_UNAVAILABLE: Docker daemon is not running")
    docker = [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "128", "--memory", "512m", "--cpus", "1",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--mount", f"type=bind,src={inputs},dst=/workspace/inputs,readonly",
        "--mount", f"type=bind,src={work},dst=/workspace/work",
        "--mount", f"type=bind,src={artifacts},dst=/workspace/artifacts",
        "--workdir", "/workspace/work", image, *command,
    ]
    try:
        result = _run_bounded_process(
            command=docker, cwd=root, timeout=timeout, output_limit=output_limit,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except FileNotFoundError as exc:
        raise OSError("ISOLATED_RUNTIME_UNAVAILABLE: Docker CLI is not installed") from exc
    returncode, _stdout, stderr, _timed_out, _truncated = result
    lowered = stderr.casefold()
    if returncode in {125, 126, 127} and any(
        marker in lowered
        for marker in ("unable to find image", "pull access denied", "cannot connect", "daemon", "no such image")
    ):
        raise OSError(f"ISOLATED_RUNTIME_UNAVAILABLE: {redact_text(stderr, 300)}")
    return result


def _container_workspace_path(root: Path, path: Path) -> str:
    relative = path.resolve().relative_to(root.resolve())
    if not relative.parts or relative.parts[0] not in {"inputs", "work", "artifacts"}:
        raise PermissionError("isolated compute may access only inputs, work, or artifacts")
    return "/workspace/" + relative.as_posix()


def _run_bounded_process(
    *, command: list[str], cwd: Path, timeout: int, output_limit: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, bool, bool]:
    """Run one Solver tool command while bounding retained stdout/stderr."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
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
