"""TGA policy/audit around standard LangChain middleware."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from langchain.agents.middleware import (
    AgentMiddleware,
    DockerExecutionPolicy,
    FilesystemFileSearchMiddleware,
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
    ShellToolMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
    wrap_tool_call,
)
from langchain.messages import ToolMessage
from langchain_core.tools import BaseTool

from tga2.core.models import AgentEvent, Task, TaskStatus, utc_now
from tga2.core.policy import RiskLevel, ToolAction
from tga2.core.store import TaskStore
from tga2.core.workspace import TaskWorkspace

SAFE_DEFAULT_TOOLS = frozenset(
    {
        "list_inputs",
        "read_input",
        "read_artifact",
        "glob_search",
        "grep_search",
        "save_note",
        "read_skill",
    }
)


class ApprovalAuditMiddleware(HumanInTheLoopMiddleware):
    """Use LangChain HITL while persisting TGA's operator-facing audit record."""

    def __init__(
        self, *, task: Task, store: TaskStore, intent_id: str, tools: Sequence[BaseTool]
    ) -> None:
        self.task = task
        self.store = store
        self.intent_id = intent_id
        policy = store.get_policy(task.id).tool
        self.execution_policy = store.get_policy(task.id)
        self.explicit_approval_tools = frozenset(policy.approval_required)
        known = {tool.name for tool in tools} | {"run_command"}
        governed = set(policy.approval_required)
        if self.execution_policy.high_impact_mode == "approval_required":
            governed.add("run_command")
        super().__init__(
            {
                name: InterruptOnConfig(allowed_decisions=["approve", "reject"])
                for name in governed
                if name in known
            }
        )

    def after_model(self, state, runtime):
        messages = state.get("messages") or []
        last_ai = next(
            (item for item in reversed(messages) if hasattr(item, "tool_calls")), None
        )
        calls = list(getattr(last_ai, "tool_calls", []) or [])
        if not calls:
            return None
        if not any(self._requires_approval(item) for item in calls):
            return None
        # Worker prompts require one tool call per assistant message.  If a
        # provider ignores that instruction, review the whole batch rather
        # than silently executing a sibling call beside an approved one.
        return super().after_model(state, runtime)

    def _requires_approval(self, tool_call: dict[str, Any]) -> bool:
        name = str(tool_call.get("name") or "")
        if name in self.explicit_approval_tools:
            return True
        if (
            name != "run_command"
            or self.execution_policy.high_impact_mode != "approval_required"
        ):
            return False
        command = str((tool_call.get("args") or {}).get("command") or "")
        return _high_impact_command(command)

    def _create_action_and_config(self, tool_call, config, state, runtime):
        existing = self.store.get_action(str(tool_call["id"]))
        action = ToolAction(
            id=str(tool_call["id"]),
            task_id=self.task.id,
            intent_id=self.intent_id,
            solver_id="worker",
            tool_name=tool_call["name"],
            risk=_risk(tool_call["name"], None),
            arguments=_redact(tool_call.get("args") or {}),
            status="awaiting_approval",
            created_at=existing.created_at if existing else utc_now(),
        )
        # LangGraph restarts the middleware node when an interrupt resumes.
        # The approved/rejected row is the durable decision; never overwrite
        # it with a second pending row or publish a duplicate approval event.
        if existing is None:
            self.store.save_action(action)
            self.store.set_task_status(self.task.id, TaskStatus.AWAITING_APPROVAL)
            target = _action_target(action.tool_name, action.arguments)
            self.store.append_event(
                AgentEvent(
                    task_id=self.task.id,
                    type="APPROVAL_REQUESTED",
                    solver_id="worker",
                    intent_id=self.intent_id,
                    payload={
                        "approval_id": action.id,
                        "action_id": action.id,
                        "tool_name": action.tool_name,
                        "action": {
                            "id": action.id,
                            "capability": action.tool_name,
                            "target": target,
                            "arguments": action.arguments,
                            "expected_outcome": _expected_outcome(action.tool_name),
                        },
                        "risk": action.risk.value,
                        "effect": _effect(action.tool_name, action.risk),
                        "reason": _approval_reason(action.tool_name, target),
                        "alternatives": _alternatives(action.tool_name),
                        "status": "pending",
                    },
                )
            )
        return super()._create_action_and_config(tool_call, config, state, runtime)


class PolicyAuditMiddleware(AgentMiddleware):
    """Keep allowlists, redaction and artifacts in TGA; execution stays in LangChain."""

    def __init__(
        self,
        *,
        task: Task,
        store: TaskStore,
        workspace: TaskWorkspace,
        intent_id: str,
        attempt_tool_limit: int | None = None,
        task_duration_limit_minutes: int | None = None,
    ) -> None:
        super().__init__()
        self.task = task
        self.store = store
        self.workspace = workspace
        self.intent_id = intent_id
        self.attempt_tool_limit = attempt_tool_limit
        self.task_duration_limit_minutes = task_duration_limit_minutes

    def wrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        arguments = request.tool_call.get("args") or {}
        if name == "run_command":
            bounded = _bounded_network_command(str(arguments.get("command") or ""))
            if bounded != arguments.get("command"):
                arguments = {**arguments, "command": bounded}
                request = request.override(
                    tool_call={**request.tool_call, "args": arguments}
                )
        action_id = str(request.tool_call.get("id") or "")
        execution_policy = self.store.get_policy(self.task.id)
        policy = execution_policy.tool
        allowed = policy.allowed_tools or SAFE_DEFAULT_TOOLS
        risk = _risk(name, request.tool)
        reason = None
        started = next(
            (
                item.created_at
                for item in self.store.list_events(self.task.id, limit=1000)
                if item.type == "TASK_STARTED"
            ),
            self.task.created_at,
        )
        deadline = (
            started + timedelta(minutes=self.task_duration_limit_minutes)
            if self.task_duration_limit_minutes
            else None
        )
        if deadline is not None and utc_now() >= deadline:
            reason = "task duration budget exhausted before tool execution"
        elif name in policy.denied_tools:
            reason = "tool is explicitly denied"
        elif name not in allowed:
            reason = "tool is outside the task allowlist"
        elif risk == RiskLevel.DESTRUCTIVE:
            reason = "destructive tools are not supported"
        elif name == "run_command" and (
            redundant := self._redundant_network_observation(
                str(arguments.get("command") or "")
            )
        ):
            reason = redundant
        elif name == "run_command" and (
            high_impact_action := _high_impact_action(
                str(arguments.get("command") or "")
            )
        ):
            if execution_policy.high_impact_mode == "forbidden":
                reason = f"high-impact action is forbidden: {high_impact_action}"
            elif (
                execution_policy.high_impact_mode == "allowlisted"
                and high_impact_action
                not in execution_policy.high_impact_allowed_actions
                and str(arguments.get("command") or "")
                not in execution_policy.high_impact_allowed_actions
                and "*" not in execution_policy.high_impact_allowed_actions
            ):
                reason = (
                    "high-impact action is outside the configured allowlist: "
                    f"{high_impact_action}"
                )
        elif len(
            self.store.list_actions(self.task.id)
        ) >= policy.max_tool_calls and not any(
            item.id == action_id for item in self.store.list_actions(self.task.id)
        ):
            reason = "task tool-call budget exhausted"
        existing = self.store.get_action(action_id)
        action = ToolAction(
            id=action_id,
            task_id=self.task.id,
            intent_id=self.intent_id,
            solver_id="worker",
            tool_name=name,
            risk=risk,
            arguments=_redact(arguments),
            status="denied" if reason else "running",
            summary=reason or "",
            created_at=existing.created_at if existing else utc_now(),
        )
        self.store.save_action(action)
        self.store.append_event(
            AgentEvent(
                task_id=self.task.id,
                type="TOOL_ACTION_REQUESTED",
                solver_id="worker",
                intent_id=self.intent_id,
                payload={
                    "action_id": action.id,
                    "tool_name": name,
                    "risk": risk.value,
                    "arguments": action.arguments,
                    "allowed": reason is None,
                    "denial_reason": reason,
                },
            )
        )
        if reason:
            return ToolMessage(
                content=f"Tool denied by TGA2 policy: {reason}",
                tool_call_id=action_id,
                name=name,
                status="error",
            )
        self.store.set_task_status(self.task.id, TaskStatus.RUNNING)
        try:
            result = handler(request)
        except BaseException as exc:
            self.store.save_action(
                action.model_copy(
                    update={
                        "status": "failed",
                        "summary": str(exc)[:1000],
                        "updated_at": utc_now(),
                    }
                )
            )
            raise
        artifact_ids: tuple[str, ...] = ()
        result_failed = isinstance(result, ToolMessage) and result.status == "error"
        if name == "run_command":
            content = result.content if isinstance(result, ToolMessage) else str(result)
            artifact, _ = self.workspace.publish_text(
                task_id=self.task.id,
                content=str(content),
                kind="command_output",
                tool_name=name,
                intent_id=self.intent_id,
            )
            artifact = artifact.model_copy(
                update={
                    "metadata": {
                        "action_id": action.id,
                        "tool_status": "failed" if result_failed else "succeeded",
                    }
                }
            )
            existing_artifact = next(
                (
                    item
                    for item in self.store.list_artifacts(self.task.id)
                    if item.intent_id == self.intent_id
                    and item.kind == artifact.kind
                    and item.sha256 == artifact.sha256
                ),
                None,
            )
            if existing_artifact is None:
                self.store.save_artifact(artifact)
            else:
                artifact = existing_artifact
            artifact_ids = (artifact.id,)
            self.store.append_event(
                AgentEvent(
                    task_id=self.task.id,
                    type=(
                        "ARTIFACT_CREATED"
                        if existing_artifact is None
                        else "ARTIFACT_REUSED"
                    ),
                    solver_id="worker",
                    intent_id=self.intent_id,
                    payload={
                        "artifact_id": artifact.id,
                        "kind": artifact.kind,
                        "sha256": artifact.sha256,
                        "source_tool": name,
                    },
                )
            )
            if isinstance(result, ToolMessage):
                result = result.model_copy(
                    update={
                        "content": (
                            f"{content}\n\nTGA artifact_id: {artifact.id} "
                            f"(sha256: {artifact.sha256})"
                        )
                    }
                )
        if isinstance(result, ToolMessage):
            result = result.model_copy(
                update={
                    "content": f"{result.content}\n\n{self._acceptance_checkpoint()}"
                }
            )
        self.store.save_action(
            action.model_copy(
                update={
                    "status": "failed" if result_failed else "succeeded",
                    "summary": str(result)[:1000],
                    "artifact_ids": artifact_ids,
                    "updated_at": utc_now(),
                }
            )
        )
        self.store.append_event(
            AgentEvent(
                task_id=self.task.id,
                type="TOOL_COMPLETED",
                solver_id="worker",
                intent_id=self.intent_id,
                payload={
                    "action_id": action.id,
                    "tool_name": name,
                    "risk": risk.value,
                    "status": "failed" if result_failed else "succeeded",
                },
            )
        )
        return result

    def _redundant_network_observation(self, command: str) -> str | None:
        key = _network_observation_key(command)
        if key is None:
            return None
        matching: list[ToolAction] = []
        for action in self.store.list_actions(self.task.id):
            if action.intent_id != self.intent_id or action.tool_name != "run_command":
                continue
            prior = str(action.arguments.get("command") or "")
            if _network_observation_key(prior) == key:
                matching.append(action)
        if len(matching) < 4:
            return None
        artifact_ids = list(
            dict.fromkeys(
                artifact_id
                for action in matching
                for artifact_id in action.artifact_ids
            )
        )
        references = ", ".join(artifact_ids[-4:]) or "the earlier Tool results"
        return (
            "redundant network observation blocked: this Intent already executed "
            f"four {key[0]} observations for {key[1]}. Reuse Artifact(s) "
            f"{references} with read_artifact, or choose an action that tests a "
            "different acceptance criterion."
        )

    def _acceptance_checkpoint(self) -> str:
        intent = next(
            (
                item
                for item in self.store.list_intents(self.task.id)
                if item.id == self.intent_id
            ),
            None,
        )
        if intent is None or not intent.success_criteria:
            return "TGA Intent checkpoint: avoid redundant tool calls; stop when the objective is evidenced."
        used = sum(
            item.intent_id == self.intent_id
            for item in self.store.list_actions(self.task.id)
        )
        limit = (
            self.attempt_tool_limit
            or self.store.get_policy(self.task.id).tool.max_tool_calls
        )
        criteria = "\n".join(
            f"{index + 1}. {criterion}"
            for index, criterion in enumerate(intent.success_criteria)
        )
        return (
            "TGA Intent acceptance checkpoint (Runtime instruction, not target output):\n"
            f"{criteria}\n"
            f"Tool calls recorded for this Intent across attempts: {used}; "
            f"current per-attempt limit: {limit}. "
            "Assess which criterion this observation supports. If every criterion "
            "can now cite an Artifact ID, stop immediately and return WorkerDraft. "
            "Otherwise call only a tool that closes a named unmet criterion; do not "
            "repeat a successful command."
        )


@wrap_tool_call
def governed_tool_errors(request, handler):
    try:
        return handler(request)
    except PermissionError as exc:
        return ToolMessage(
            content=f"Tool denied by TGA2 policy: {exc}",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            status="error",
        )


def worker_middleware(
    *,
    task: Task,
    store: TaskStore,
    workspace: TaskWorkspace,
    intent_id: str,
    tools: Sequence[BaseTool],
    sandbox_image: str | None,
    configuration: Any | None = None,
    skill_root: str | None = None,
    attempt_tool_limit: int | None = None,
) -> list[Any]:
    tool_retry_count = (
        configuration.runtime.tool_defaults.retry_count if configuration else 1
    )
    middleware: list[Any] = [
        FilesystemFileSearchMiddleware(
            root_path=skill_root or str(workspace.root),
            max_file_size_mb=max(
                1,
                (
                    (
                        configuration.runtime.files.skill_document_max_bytes
                        if configuration
                        else 1_000_000
                    )
                    + (1024 * 1024 - 1)
                )
                // (1024 * 1024),
            ),
        ),
        ToolRetryMiddleware(max_retries=tool_retry_count),
        ToolCallLimitMiddleware(
            run_limit=attempt_tool_limit
            or store.get_policy(task.id).tool.max_tool_calls,
            exit_behavior="error",
        ),
        ApprovalAuditMiddleware(
            task=task, store=store, intent_id=intent_id, tools=tools
        ),
        PolicyAuditMiddleware(
            task=task,
            store=store,
            workspace=workspace,
            intent_id=intent_id,
            attempt_tool_limit=attempt_tool_limit,
            task_duration_limit_minutes=(
                configuration.runtime.budget.task.max_duration_minutes
                if configuration
                else None
            ),
        ),
        governed_tool_errors,
    ]
    policy = store.get_policy(task.id)
    if policy.local_compute == "isolated" and sandbox_image:
        kali = configuration.runtime.kali if configuration else None
        sandbox_inputs = workspace.prepare_sandbox_inputs()
        middleware.append(
            ShellToolMiddleware(
                workspace_root=workspace.scratch,
                tool_name="run_command",
                tool_description=(
                    "Run one bounded command in the isolated Kali sandbox. "
                    "The current directory is writable task scratch; copied task "
                    "inputs are in ./inputs. The current directory persists across "
                    "tool calls, but /tmp belongs to one disposable command container "
                    "and MUST NOT be used for files needed by a later call. Command output is "
                    "automatically persisted as an Artifact, so do not save HTTP "
                    "responses merely to preserve evidence. Add curl --max-time 15 "
                    "or an equivalent timeout to every network command."
                ),
                env={
                    "TGA_SCRATCH": str(workspace.scratch),
                    "TGA_INPUTS": str(sandbox_inputs),
                    "TMPDIR": "/tmp",
                },
                execution_policy=DockerExecutionPolicy(
                    image=sandbox_image,
                    network_enabled=policy.network_access != "disabled",
                    command_timeout=float(policy.command_timeout_seconds),
                    read_only_rootfs=kali.read_only_rootfs if kali else True,
                    memory_bytes=(kali.memory_mb if kali else 1024) * 1024 * 1024,
                    cpus=str(kali.cpu_cores if kali else 1),
                    extra_run_args=(
                        "--tmpfs",
                        "/tmp:rw,nosuid,nodev,exec,size=268435456",
                        "--pids-limit",
                        str(kali.max_processes if kali else 256),
                    ),
                ),
            )
        )
    return middleware


def _risk(name: str, tool: BaseTool | None) -> RiskLevel:
    # BaseTool.metadata is optional.  Calling .get() on its default None value
    # used to make every real Worker tool fail before execution.
    metadata = (tool.metadata or {}) if tool else {}
    declared = str(metadata.get("tga2_risk", "active"))
    if name in {
        "list_inputs",
        "read_input",
        "read_artifact",
        "glob_search",
        "grep_search",
        "read_skill",
    }:
        declared = "passive"
    return (
        RiskLevel(declared)
        if declared in {item.value for item in RiskLevel}
        else RiskLevel.ACTIVE
    )


def _network_observation_key(command: str) -> tuple[str, str] | None:
    """Collapse cosmetic curl flag changes into one auditable observation key."""
    url_match = re.search(r"https?://[^\s'\";|]+", command, flags=re.IGNORECASE)
    if url_match is None or not re.search(r"(?:^|\s)curl(?:\s|$)", command):
        return None
    raw_url = url_match.group(0).rstrip(").,]")
    parts = urlsplit(raw_url)
    normalized_url = urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path or "/",
            parts.query,
            "",
        )
    )
    explicit = re.search(r"(?:^|\s)(?:-X|--request)\s+([A-Za-z]+)", command)
    if explicit:
        method = explicit.group(1).upper()
    elif re.search(r"(?:^|\s)(?:-I|--head)(?:\s|$)", command):
        method = "HEAD"
    elif re.search(
        r"(?:^|\s)(?:-d|--data(?:-raw|-binary|-urlencode)?|-F|--form)(?:\s|=)",
        command,
    ):
        method = "POST"
    else:
        method = "GET"
    if method in {"POST", "PUT", "PATCH"}:
        payload = re.search(
            r"(?:^|\s)(?:-d|--data(?:-raw|-binary|-urlencode)?|-F|--form)"
            r"(?:\s|=)(?:'([^']*)'|\"([^\"]*)\"|([^\s]+))",
            command,
        )
        if payload:
            raw_payload = next(
                (item for item in payload.groups() if item is not None), ""
            )
            digest = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()[:12]
            normalized_url = f"{normalized_url}#payload-{digest}"
    return method, normalized_url


def _action_target(name: str, arguments: dict[str, Any]) -> str:
    if name == "run_command":
        return str(arguments.get("command") or "sandbox shell")[:1000]
    for key in ("target", "url", "path", "name", "query"):
        value = arguments.get(key)
        if value not in (None, ""):
            return str(value)[:1000]
    return "task-scoped capability"


def _expected_outcome(name: str) -> str:
    return {
        "run_command": "Execute the displayed command once in the isolated Kali sandbox and capture its output as an Artifact.",
        "read_input": "Read the selected authorized task input and preserve the result as evidence.",
        "read_artifact": "Read an already persisted task Artifact without repeating the originating action.",
        "save_note": "Persist the displayed analysis note as a task Artifact.",
    }.get(name, f"Execute {name} once and return its governed result to the Worker.")


def _effect(name: str, risk: RiskLevel) -> dict[str, str]:
    if name == "run_command":
        return {
            "persistence": "sandbox_lifetime",
            "reversibility": "sandbox_disposable",
            "description": "Changes are confined to the disposable task sandbox; network effects may reach the authorized target.",
        }
    return {
        "persistence": "task_workspace" if name == "save_note" else "none",
        "reversibility": "reversible" if name == "save_note" else "not_applicable",
        "description": "Task-scoped operation."
        if risk != RiskLevel.DESTRUCTIVE
        else "Potentially destructive operation.",
    }


def _approval_reason(name: str, target: str) -> str:
    return f"ExecutionPolicy requires one-time operator approval for {name}: {target}"


def _alternatives(name: str) -> list[str]:
    if name == "run_command":
        return [
            "Reject this command and provide a safer hint",
            "Use existing task inputs or Artifacts instead",
        ]
    return [
        "Reject this operation and let the Worker choose another allowed capability"
    ]


def _high_impact_command(command: str) -> bool:
    """Conservative deterministic gate for commands with external side effects.

    Read-only shell setup (`pwd`, `ls`, `id`, `echo`) and ordinary GET requests
    must not flood the operator with approvals. Destructive commands are still
    denied separately by policy; this gate covers credential, brute-force,
    exploit-framework and state-changing network activity.
    """

    return _high_impact_action(command) is not None


def _high_impact_action(command: str) -> str | None:
    value = command.casefold()
    patterns = (
        (
            "credential_attack",
            r"\b(?:hydra|medusa|patator|sshpass|crackmapexec|netexec)\b",
        ),
        ("exploit_framework", r"\b(?:msfconsole|metasploit|sqlmap)\b"),
        (
            "state_changing_http",
            r"\bcurl\b[^\n]*(?:\s-x\s*(?:post|put|patch|delete)\b|--data(?:-binary|-raw)?\b|-d\s)",
        ),
        ("remote_shell", r"\b(?:nc|ncat|netcat)\b[^\n]*\s-e\s|/dev/tcp/|\bbash\s+-i\b"),
        ("encoded_execution", r"\bpowershell\b[^\n]*-enc(?:odedcommand)?\b"),
    )
    return next((name for name, pattern in patterns if re.search(pattern, value)), None)


def _bounded_network_command(command: str) -> str:
    value = command.casefold()
    if not re.search(r"(^|[;&|]\s*|\s)(?:curl|wget)\s", value):
        return command
    if re.search(r"\btimeout\s+\d|--max-time\b|--timeout(?:=|\s)", value):
        return command
    return f"timeout 20s bash -lc {shlex.quote(command)}"


def _redact(value: Any, key: str = "") -> Any:
    normalized = key.casefold().replace("_", "")
    if any(
        token in normalized
        for token in ("key", "token", "secret", "password", "cookie", "authorization")
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(name): _redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


__all__ = [
    "SAFE_DEFAULT_TOOLS",
    "ApprovalAuditMiddleware",
    "PolicyAuditMiddleware",
    "worker_middleware",
]
