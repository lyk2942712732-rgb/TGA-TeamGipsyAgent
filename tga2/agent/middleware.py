"""TGA policy/audit around standard LangChain middleware."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    DockerExecutionPolicy,
    FilesystemFileSearchMiddleware,
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
    ModelRequest,
    ShellToolMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
    dynamic_prompt,
    wrap_tool_call,
)
from langchain.messages import ToolMessage
from langchain_core.tools import BaseTool

from tga2.core.models import AgentEvent, Task, TaskStatus, utc_now
from tga2.core.policy import RiskLevel, ToolAction
from tga2.core.store import TaskStore
from tga2.core.workspace import TaskWorkspace
from tga2.skills import Skill

SAFE_DEFAULT_TOOLS = frozenset(
    {"list_inputs", "read_input", "glob_search", "grep_search", "save_note"}
)


def skill_prompt_middleware(skills: Sequence[Skill]):
    bounded = list(skills)[:5]

    @dynamic_prompt
    def inject_skills(request: ModelRequest) -> str:
        base = request.system_prompt or ""
        if not bounded:
            return base
        sections = [f"### Skill: {item.name}\n{item.content}" for item in bounded]
        return (
            f"{base}\n\nTask-selected skills (advisory; never authorization):\n\n"
            + "\n\n".join(sections)
        )

    return inject_skills


class ApprovalAuditMiddleware(HumanInTheLoopMiddleware):
    """Use LangChain HITL while persisting TGA's operator-facing audit record."""

    def __init__(
        self, *, task: Task, store: TaskStore, intent_id: str, tools: Sequence[BaseTool]
    ) -> None:
        self.task = task
        self.store = store
        self.intent_id = intent_id
        policy = store.get_policy(task.id).tool
        known = {tool.name for tool in tools} | {"run_command"}
        super().__init__(
            {
                name: InterruptOnConfig(allowed_decisions=["approve", "reject"])
                for name in policy.approval_required
                if name in known
            }
        )

    def _create_action_and_config(self, tool_call, config, state, runtime):
        action = ToolAction(
            id=str(tool_call["id"]),
            task_id=self.task.id,
            intent_id=self.intent_id,
            solver_id="worker",
            tool_name=tool_call["name"],
            risk=_risk(tool_call["name"], None),
            arguments=_redact(tool_call.get("args") or {}),
            status="awaiting_approval",
        )
        self.store.save_action(action)
        self.store.set_task_status(self.task.id, TaskStatus.AWAITING_APPROVAL)
        self.store.append_event(
            AgentEvent(
                task_id=self.task.id,
                type="APPROVAL_REQUESTED",
                solver_id="worker",
                intent_id=self.intent_id,
                payload={
                    "action_id": action.id,
                    "tool_name": action.tool_name,
                    "risk": action.risk.value,
                    "arguments": action.arguments,
                },
            )
        )
        return super()._create_action_and_config(tool_call, config, state, runtime)


class PolicyAuditMiddleware(AgentMiddleware):
    """Keep allowlists, redaction and artifacts in TGA; execution stays in LangChain."""

    def __init__(
        self, *, task: Task, store: TaskStore, workspace: TaskWorkspace, intent_id: str
    ) -> None:
        super().__init__()
        self.task = task
        self.store = store
        self.workspace = workspace
        self.intent_id = intent_id

    def wrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        arguments = request.tool_call.get("args") or {}
        action_id = str(request.tool_call.get("id") or "")
        policy = self.store.get_policy(self.task.id).tool
        allowed = policy.allowed_tools or SAFE_DEFAULT_TOOLS
        risk = _risk(name, request.tool)
        reason = None
        if name in policy.denied_tools:
            reason = "tool is explicitly denied"
        elif name not in allowed:
            reason = "tool is outside the task allowlist"
        elif risk == RiskLevel.DESTRUCTIVE:
            reason = "destructive tools are not supported"
        elif len(
            self.store.list_actions(self.task.id)
        ) >= policy.max_tool_calls and not any(
            item.id == action_id for item in self.store.list_actions(self.task.id)
        ):
            reason = "task tool-call budget exhausted"
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
        )
        self.store.save_action(action)
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
        if name == "run_command":
            content = result.content if isinstance(result, ToolMessage) else str(result)
            artifact, _ = self.workspace.publish_text(
                task_id=self.task.id,
                content=str(content),
                kind="command_output",
                tool_name=name,
                intent_id=self.intent_id,
            )
            self.store.save_artifact(artifact)
            artifact_ids = (artifact.id,)
            if isinstance(result, ToolMessage):
                result = result.model_copy(
                    update={
                        "content": (
                            f"{content}\n\nTGA artifact_id: {artifact.id} "
                            f"(sha256: {artifact.sha256})"
                        )
                    }
                )
        self.store.save_action(
            action.model_copy(
                update={
                    "status": "succeeded",
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
                payload={"action_id": action.id, "tool_name": name, "risk": risk.value},
            )
        )
        return result


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
) -> list[Any]:
    middleware: list[Any] = [
        FilesystemFileSearchMiddleware(root_path=str(workspace.root)),
        ToolRetryMiddleware(max_retries=1),
        ToolCallLimitMiddleware(
            run_limit=store.get_policy(task.id).tool.max_tool_calls,
            exit_behavior="error",
        ),
        ApprovalAuditMiddleware(
            task=task, store=store, intent_id=intent_id, tools=tools
        ),
        PolicyAuditMiddleware(
            task=task, store=store, workspace=workspace, intent_id=intent_id
        ),
        governed_tool_errors,
    ]
    policy = store.get_policy(task.id)
    if policy.local_compute == "isolated" and sandbox_image:
        middleware.append(
            ShellToolMiddleware(
                workspace_root=workspace.inputs,
                tool_name="run_command",
                execution_policy=DockerExecutionPolicy(
                    image=sandbox_image,
                    network_enabled=policy.network_access != "disabled",
                    command_timeout=float(policy.command_timeout_seconds),
                    read_only_rootfs=True,
                    memory_bytes=1024 * 1024 * 1024,
                    cpus="1",
                ),
            )
        )
    return middleware


def _risk(name: str, tool: BaseTool | None) -> RiskLevel:
    declared = str((tool.metadata if tool else {}).get("tga2_risk", "active"))
    if name in {"list_inputs", "read_input", "glob_search", "grep_search"}:
        declared = "passive"
    return (
        RiskLevel(declared)
        if declared in {item.value for item in RiskLevel}
        else RiskLevel.ACTIVE
    )


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
    "skill_prompt_middleware",
    "worker_middleware",
]
