"""Controlled bridge from runtime actions to concrete capabilities.

This module deliberately has no knowledge of strategy state, flags, or event storage.
It turns one validated ``ActionSpec`` into one ``ActionResult`` and leaves
confirmation and persistence orchestration to the caller.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from pydantic import ValidationError

from tga.contracts import ActionResult, ActionSpec, TGAError, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.indexing import build_artifact_index, retrieve_segments
from tga.tools.rate_limit import RateLimiter
from .http_session import HTTPSessionRegistry
from .registry import CapabilityRegistry, build_default_registry
from .schemas import ArtifactInspectArguments, WorkspaceReadArguments
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
    """Legacy host resource reader; execution capabilities fail closed."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        registry: CapabilityRegistry | None = None,
        budget: ExecutionBudget | None = None,
        http_sessions: HTTPSessionRegistry | None = None,
        sandbox_manager: Any | None = None,
        fencing_token_provider: Any | None = None,
        execution_context: Any | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.registry = registry or build_default_registry()
        self.budget = budget or ExecutionBudget()
        self.http_sessions = http_sessions or HTTPSessionRegistry()
        self.sandbox_manager = sandbox_manager
        self.fencing_token_provider = fencing_token_provider or (lambda _solver_id: 1)
        self.execution_context = execution_context

    def close_http_sessions(self, *, task_id: str, solver_id: str | None = None) -> int:
        return self.http_sessions.destroy(task_id=task_id, solver_id=solver_id)

    def http_session_snapshot(self, *, task_id: str, solver_id: str) -> dict:
        return self.http_sessions.snapshot(task_id=task_id, solver_id=solver_id)

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
        """Return a structured outcome; never mutate strategy state or confirm a flag."""
        if self.execution_context is not None:
            self.execution_context.assert_active()
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

        if action.capability not in {"workspace.read", "artifact.inspect"}:
            return self._reject(
                action,
                "EXECUTION_BACKEND_REQUIRED",
                "Execution capabilities must be routed through ToolGovernanceGateway and the Kali sandbox backend.",
            )

        if _risk_rank(action.risk) < _risk_rank(registered.spec.risk):
            return self._reject(action, "RISK_UNDERSPECIFIED", "action risk is lower than capability risk")
        if isinstance(arguments, WorkspaceReadArguments):
            return self._workspace_read(
                task=task, action=action, arguments=arguments, workspace=workspace
            )
        if isinstance(arguments, ArtifactInspectArguments):
            return self._artifact_inspect(task=task, action=action, arguments=arguments)
        return self._reject(
            action,
            "CAPABILITY_NOT_IMPLEMENTED",
            f"{action.capability} is registered but not enabled by this resource reader",
        )

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
    return (
        f"{parsed.hostname.lower()}:{parsed.port}"
        if parsed.port
        else parsed.hostname.lower()
    )
