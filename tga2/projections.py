"""Stable projection from compact product state to the existing v2 frontend."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tga2.config import RuntimeSettings


def event_projection(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 6,
        "id": f"evt-{event['seq']}",
        "task_id": event["task_id"],
        "seq": event["seq"],
        "type": event["type"],
        "solver_id": event.get("solver_id"),
        "intent_id": event.get("intent_id"),
        "payload": event.get("payload") or {},
        "created_at": event["created_at"],
    }


def runtime_snapshot_projection(
    raw: dict[str, Any],
    events: list[dict[str, Any]],
    runtime: RuntimeSettings | None = None,
) -> dict[str, Any]:
    task = raw["task"]
    task_id = task["id"]
    status = task["status"]
    intents = [_intent(item) for item in raw.get("intents", [])]
    if status == "failed":
        intents = [
            {**item, "status": "failed"}
            if item["status"] in {"running", "review", "reviewing"}
            else item
            for item in intents
        ]
    artifacts = [_artifact(item) for item in raw.get("artifacts", [])]
    claims = [_claim(item) for item in raw.get("evidence_claims", [])]
    findings = [_finding(item) for item in raw.get("findings", [])]
    policy = raw.get("policy") or {}
    actions = [_action(item) for item in raw.get("actions", [])]
    approvals = [
        _approval(item)
        for item in raw.get("actions", [])
        if item["status"] == "awaiting_approval"
    ]
    projected_events = [event_projection(item) for item in events]
    started = next(
        (item["created_at"] for item in events if item["type"] == "TASK_STARTED"), None
    )
    finished = next(
        (
            item["created_at"]
            for item in reversed(events)
            if item["type"]
            in {
                "TASK_COMPLETED",
                "TASK_COMPLETED_WITH_LIMITATIONS",
                "TASK_FAILED",
                "TASK_CANCELLED",
            }
        ),
        None,
    )
    waiting_for_user = next(
        (
            item
            for item in reversed(events)
            if item["type"] in {"USER_INPUT_REQUIRED", "USER_INPUT_RECEIVED"}
        ),
        None,
    )
    awaiting_user = bool(
        waiting_for_user and waiting_for_user["type"] == "USER_INPUT_REQUIRED"
    )
    # A user-input checkpoint supersedes old approval rows left by versions
    # that did not correctly resume the nested Worker graph.
    if awaiting_user:
        approvals = []
    projected_status = (
        "awaiting_user_input"
        if awaiting_user
        else "awaiting_approval"
        if approvals
        else status
    )
    solvers = _solvers(
        raw.get("solver_runs", []),
        task_id,
        events,
        projected_status,
        intents,
        approvals,
    )
    model_call_event_types = {
        "PLAN_CREATED",
        "WORKER_ATTEMPT_COMPLETED",
        "REVIEW_COMPLETED",
        "SUPERVISOR_DECIDED",
        "REPORT_GENERATED",
    }
    model_calls = sum(
        int((item.get("payload") or {}).get("model_calls") or 0)
        for item in events
        if item["type"] in model_call_event_types
    )
    failed_calls = next(
        (
            int((item.get("payload") or {}).get("model_calls") or 0)
            or _model_limit_from_message((item.get("payload") or {}).get("message"))
            for item in reversed(events)
            if item["type"] == "TASK_FAILED"
        ),
        0,
    )
    model_calls += failed_calls
    session = {
        "status": projected_status,
        "supervisor_solver_id": "supervisor" if solvers else None,
        "active_solver_count": sum(
            item["status"] in {"running", "awaiting_approval", "awaiting_user_input"}
            for item in solvers
        ),
        "max_active_workers": runtime.graph.max_active_workers if runtime else 1,
        "task_budget_usage": {
            "turns": model_calls,
            "model_calls": model_calls,
            "tool_calls": len(actions),
            "artifacts": len(artifacts),
        },
        "stop_reason": _stop_reason(events, awaiting_user),
        "user_input_request": (
            {
                "question": (waiting_for_user.get("payload") or {}).get("question"),
                "reason": (waiting_for_user.get("payload") or {}).get("reason"),
                "intent_id": waiting_for_user.get("intent_id"),
                "requested_at": waiting_for_user.get("created_at"),
            }
            if awaiting_user and waiting_for_user
            else None
        ),
        "turn_count": model_calls,
        "max_turns": runtime.budget.task.max_model_calls if runtime else 60,
        "timestamps": {
            "created_at": task["created_at"],
            "started_at": started,
            "finished_at": finished,
            "updated_at": task["updated_at"],
        },
    }
    team = {
        "task_id": task_id,
        "status": projected_status,
        "supervisor_solver_id": session["supervisor_solver_id"],
        "max_active_workers": runtime.graph.max_active_workers if runtime else 1,
        "max_total_solvers": runtime.graph.max_total_solvers if runtime else 4,
        "active_solver_count": session["active_solver_count"],
        "solver_ids": [item["solver_id"] for item in solvers],
        "version": 1,
        "timestamps": session["timestamps"],
    }
    task_spec = {
        "task_id": task_id,
        "objective": task["spec"]["objective"],
        "instructions": _directives(
            task_id, "instruction", task["spec"]["instructions"]
        ),
        "constraints": _directives(task_id, "constraint", task["spec"]["constraints"]),
        "success_criteria": _directives(
            task_id, "success_criterion", task["spec"]["success_criteria"]
        ),
        "resources": task["spec"]["resources"],
        "legacy_import": False,
        "provenance": {"runtime": "tga2"},
    }
    lifecycle = {
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "status": projected_status,
        "turn_count": session["turn_count"],
        "max_turns": runtime.budget.task.max_model_calls if runtime else 60,
        "started_at": started,
        "finished_at": finished,
        "stop_reason": session["stop_reason"],
        "active_solvers": session["active_solver_count"],
        "pending_approvals": len(approvals),
        "intent_total": len(intents),
        "intent_completed": sum(item["status"] == "completed" for item in intents),
        "flags": 0,
        "findings": len(findings),
        "artifacts": len(artifacts),
        "needs_attention": projected_status
        in {"awaiting_approval", "awaiting_user_input"},
        "latest_event": (
            {key: projected_events[-1][key] for key in ("seq", "type", "created_at")}
            if projected_events
            else None
        ),
    }
    return {
        "schema_version": 6,
        "task_id": task_id,
        "task": {
            **task,
            "goal": task["spec"]["objective"],
            "schema_version": 6,
            "task_spec": task_spec,
        },
        "task_spec": task_spec,
        "lifecycle": lifecycle,
        "input_summary": {
            "prompt_present": bool(task["spec"]["instructions"]),
            "prompt_preview": "\n".join(task["spec"]["instructions"])[:1000],
            "file_count": len(task["spec"]["resources"]),
            "files": task["spec"]["resources"],
            "task_entry_url": _first_url(task),
        },
        "config_snapshot": {
            "mode_config": task["spec"].get("mode_options") or {"mode": task["mode"]},
            "execution_policy": policy,
            "execution_budget": {
                **(runtime.budget.model_dump(mode="json") if runtime else {}),
                "max_turns": runtime.budget.task.max_model_calls if runtime else 60,
            },
            "model": {
                role: settings.model.model_dump(mode="json")
                for role, settings in runtime.roles.items()
            }
            if runtime
            else None,
            "mcp_capabilities": {
                "tool_names": sorted(
                    (policy.get("tool") or {}).get("allowed_tools") or []
                )
            },
            "agent_prompt": None,
        },
        "session": session,
        "team": team,
        "solvers": solvers,
        "intents": intents,
        "worker_results": _worker_results(events),
        "knowledge": _knowledge(events),
        "artifacts": artifacts,
        "evidence_claims": claims,
        "findings": findings,
        "actions": actions,
        "approvals": approvals,
        "retrieval_runs": [],
        "events": projected_events,
        "latest_seq": projected_events[-1]["seq"] if projected_events else 0,
        "events_page": {"has_more": False},
        "global_plan": {
            "task_id": task_id,
            "intent_ids": [item["intent_id"] for item in intents],
            "version": (raw.get("plan") or {}).get("version", 0),
            "summary": (raw.get("plan") or {}).get("summary", ""),
            "success_criteria": [
                f"{item['title']}: {criterion}"
                for item in intents
                for criterion in item.get("success_criteria", [])
            ],
            "status": "completed"
            if status in {"completed", "completed_with_limitations"}
            else "active",
        },
        "challenge": {},
        "flags": [],
        "artifact_indexes": [],
        "report": raw.get("report"),
    }


def task_list_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
    task = snapshot["task"]
    lifecycle = snapshot["lifecycle"]
    severity_order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    severities = [item["severity"] for item in snapshot.get("findings", [])]
    return {
        "schema_version": 6,
        "task_id": task["id"],
        "name": task["name"],
        "mode": task["mode"],
        "target_summary": task["goal"],
        "target_count": 1,
        "hint_count": len(task["spec"]["instructions"]),
        "solver_total": len(snapshot.get("solvers", [])),
        "highest_severity": max(severities, key=severity_order.get)
        if severities
        else None,
        **lifecycle,
    }


def _directives(task_id: str, kind: str, values: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{kind}-{index}",
            "task_id": task_id,
            "kind": kind,
            "content": value,
            "source": "user",
        }
        for index, value in enumerate(values, 1)
    ]


def _stop_reason(events: list[dict[str, Any]], awaiting_user: bool) -> str | None:
    if awaiting_user:
        return "user_input_required"
    terminal = next(
        (
            item
            for item in reversed(events)
            if item["type"] in {"TASK_FAILED", "TASK_CANCELLED"}
        ),
        None,
    )
    if terminal is None:
        return None
    payload = terminal.get("payload") or {}
    return str(payload.get("message") or payload.get("reason") or terminal["type"])


def _model_limit_from_message(value: Any) -> int:
    match = re.search(r"run limit \((\d+)/(\d+)\)", str(value or ""), re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _first_url(task: dict[str, Any]) -> str | None:
    spec = task.get("spec") or {}
    text = "\n".join(
        [
            str(spec.get("objective") or ""),
            *(str(item) for item in spec.get("instructions") or []),
        ]
    )
    match = re.search(r"https?://[^\s<>'\"]+", text, flags=re.IGNORECASE)
    return match.group(0).rstrip(".,);]") if match else None


def _solvers(
    runs: list[dict[str, Any]],
    task_id: str,
    events: list[dict[str, Any]],
    task_status: str,
    intents: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    roles = ("supervisor", "worker", "reviewer", "reporter")
    latest_run = {run["solver_id"]: run for run in runs}
    observed = {
        str(item.get("solver_id"))
        for item in events
        if item.get("solver_id") in roles
    } | set(latest_run)
    current_intent = next(
        (
            item
            for item in intents
            if item["status"] in {"running", "review", "reviewing", "failed"}
        ),
        None,
    )
    terminal = task_status in {
        "completed",
        "completed_with_limitations",
        "failed",
        "cancelled",
    }
    result: list[dict[str, Any]] = []
    for role in roles:
        if role not in observed:
            continue
        role_events = [item for item in events if item.get("solver_id") == role]
        activity = next(
            (
                item
                for item in reversed(role_events)
                if item["type"] == "SOLVER_STATUS_CHANGED"
            ),
            None,
        )
        run = latest_run.get(role) or {}
        assigned_intent = (
            (activity or {}).get("intent_id")
            or run.get("intent_id")
        )
        if role == "supervisor":
            assigned_intent = (activity or {}).get("intent_id")
        status_value, summary = _solver_state(
            role,
            role_events,
            activity,
            task_status,
            bool(approvals),
            terminal,
            current_intent,
            str(run.get("summary") or ""),
        )
        model_calls = sum(
            int((item.get("payload") or {}).get("model_calls") or 0)
            for item in role_events
        )
        tool_calls = sum(
            item["type"] == "TOOL_ACTION_REQUESTED" for item in role_events
        )
        created_at = (
            role_events[0]["created_at"]
            if role_events
            else run.get("created_at")
        )
        updated_at = (
            role_events[-1]["created_at"]
            if role_events
            else run.get("updated_at")
        )
        result.append(
            {
                "task_id": task_id,
                "solver_id": role,
                "definition_id": role,
                "orchestration_role": role,
                "specialties": [],
                "parent_solver_id": None if role == "supervisor" else "supervisor",
                "assigned_intent_id": assigned_intent,
                "status": status_value,
                "current_summary": summary,
                "model_snapshot": {},
                "capability_binding": {},
                "budget_usage": {
                    "input_tokens": int(run.get("input_tokens") or 0),
                    "output_tokens": int(run.get("output_tokens") or 0),
                    "model_calls": model_calls,
                    "tool_calls": tool_calls,
                },
                "timestamps": {
                    "created_at": created_at,
                    "updated_at": updated_at,
                },
            }
        )
    return result


def _solver_state(
    role: str,
    role_events: list[dict[str, Any]],
    activity: dict[str, Any] | None,
    task_status: str,
    has_approvals: bool,
    terminal: bool,
    current_intent: dict[str, Any] | None,
    fallback_summary: str,
) -> tuple[str, str]:
    if terminal:
        activity_status = str((activity or {}).get("payload", {}).get("status") or "")
        if task_status == "failed":
            owns_failed_intent = bool(
                current_intent
                and current_intent.get("status") == "failed"
                and current_intent.get("assigned_solver_id") == role
            )
            return (
                "failed"
                if activity_status == "failed" or owns_failed_intent
                else "stopped",
                _event_summary(activity)
                or (
                    "执行阶段失败"
                    if activity_status == "failed" or owns_failed_intent
                    else "任务已终止"
                ),
            )
        return ("completed", _event_summary(activity) or fallback_summary)
    if task_status == "awaiting_user_input":
        return (
            "awaiting_user_input" if role == "supervisor" else "waiting",
            _event_summary(activity) or ("等待用户回答" if role == "supervisor" else "等待 Supervisor"),
        )
    if has_approvals:
        return (
            "awaiting_approval" if role == "worker" else "waiting",
            _event_summary(activity) or ("等待操作审批" if role == "worker" else "等待 Worker"),
        )
    if activity:
        return str((activity.get("payload") or {}).get("status") or "waiting"), _event_summary(activity) or fallback_summary
    latest_type = role_events[-1]["type"] if role_events else ""
    if role == "worker" and latest_type in {"INTENT_STARTED", "WORKER_ATTEMPT_STARTED", "TOOL_ACTION_REQUESTED", "TOOL_COMPLETED"}:
        return "running", _event_summary(role_events[-1]) or "正在执行当前 Intent"
    if role == "reviewer" and latest_type == "REVIEW_COMPLETED":
        return "waiting", _event_summary(role_events[-1]) or "证据审查已完成"
    if role == "reporter" and latest_type == "REPORT_GENERATED":
        return "completed", "最终报告已生成"
    if current_intent and role == "worker":
        return "running", fallback_summary or f"正在处理 {current_intent['title']}"
    return "waiting", fallback_summary or "等待上游结果"


def _event_summary(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    payload = event.get("payload") or {}
    return str(
        payload.get("summary")
        or payload.get("reason")
        or payload.get("feedback")
        or payload.get("question")
        or ""
    )[:1000]


def _worker_results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "result_id": f"worker-{item['intent_id']}-{(item.get('payload') or {}).get('attempt', 1)}",
            "solver_id": "worker",
            "intent_id": item.get("intent_id"),
            "status": (item.get("payload") or {}).get(
                "completion_status", "incomplete"
            ),
            "summary": (item.get("payload") or {}).get("summary", ""),
            "artifact_ids": [],
            "evidence_claim_ids": (item.get("payload") or {}).get("claim_ids", []),
            "knowledge_ids": [],
            "finding_ids": [],
            "limitations": (item.get("payload") or {}).get("limitations", []),
            "completion_status": (item.get("payload") or {}).get(
                "completion_status", "incomplete"
            ),
            "criterion_assessments": (item.get("payload") or {}).get(
                "criterion_assessments", []
            ),
            "budget_usage": {"model_calls": (item.get("payload") or {}).get("model_calls", 0)},
        }
        for item in events
        if item["type"] == "WORKER_ATTEMPT_COMPLETED"
    ]


def _knowledge(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "knowledge_id": f"skill-{item['seq']}",
            "scope": "intent",
            "target_id": item.get("intent_id"),
            "status": "read",
            "kind": "skill_document",
            "content_preview": f"{(item.get('payload') or {}).get('skill_name', '')}/{(item.get('payload') or {}).get('path', '')}",
            "content_sha256": (item.get("payload") or {}).get("sha256", ""),
            "created_by_solver_id": item.get("solver_id"),
            "created_at": item.get("created_at"),
        }
        for item in events
        if item["type"] == "SKILL_DOCUMENT_READ"
    ]


def _intent(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": item["task_id"],
        "intent_id": item["id"],
        "kind": "task",
        "title": item["title"],
        "objective": item["objective"],
        "status": item["status"],
        "assigned_solver_id": item["assigned_solver_id"],
        "dependencies": item["dependencies"],
        "priority": item["priority"],
        "success_criteria": item.get("success_criteria", []),
        "expected_evidence": item.get("expected_evidence", []),
        "stop_conditions": item.get("stop_conditions", []),
        "allowed_tools": item.get("allowed_tools", []),
        "budget": {},
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _artifact(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": item["id"],
        "intent_id": item.get("intent_id"),
        "kind": item["kind"],
        "media_type": item["media_type"],
        "tool": item.get("tool_name"),
        "target": None,
        "sha256": item["sha256"],
        "created_at": item["created_at"],
        "path": item["path"],
    }


def _claim(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": item["id"],
        "statement_preview": item["statement"],
        "artifact_id": item["artifact_id"],
        "locator": item["locator"],
        "status": item["status"],
        "created_by_solver_id": item["created_by"],
        "reviewed_by_solver_id": item.get("reviewed_by"),
        "created_at": item["created_at"],
        "reviewed_at": item.get("reviewed_at"),
    }


def _finding(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": item["id"],
        "title": item["title"],
        "description_preview": item["description"],
        "target": None,
        "severity": item["severity"],
        "status": item["status"],
        "evidence_claim_ids": item["evidence_claim_ids"],
        "created_by_solver_id": "reviewer",
        "created_at": item["created_at"],
        "reviewed_at": item["created_at"],
    }


def _action(item: dict[str, Any]) -> dict[str, Any]:
    target = _projected_action_target(item)
    return {
        "id": item["id"],
        "action_id": item["id"],
        "solver_id": item["solver_id"],
        "intent_id": item.get("intent_id"),
        "capability": item["tool_name"],
        "target": target,
        "risk": item["risk"],
        "effect": _projected_effect(item),
        "arguments": item["arguments"],
        "status": item["status"],
        "summary": item["summary"],
        "artifact_ids": item["artifact_ids"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _approval(item: dict[str, Any]) -> dict[str, Any]:
    target = _projected_action_target(item)
    return {
        "approval_id": item["id"],
        "solver_id": item["solver_id"],
        "intent_id": item.get("intent_id"),
        "action_id": item["id"],
        "action": {
            "id": item["id"],
            "capability": item["tool_name"],
            "target": target,
            "arguments": item["arguments"],
            "expected_outcome": _expected_outcome(item["tool_name"]),
            "status": "pending",
        },
        "risk": item["risk"],
        "effect": _projected_effect(item),
        "reason": f"ExecutionPolicy requires one-time approval for {item['tool_name']}.",
        "alternatives": ["拒绝本次操作并向 Worker 提供更安全的提示", "改用已有输入、Artifact 或只读能力"],
        "deadline": "",
        "status": "pending",
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _projected_action_target(item: dict[str, Any]) -> str:
    arguments = item.get("arguments") or {}
    if item.get("tool_name") == "run_command":
        return str(arguments.get("command") or "Kali sandbox")[:1000]
    for key in ("target", "url", "path", "name", "query"):
        if arguments.get(key) not in (None, ""):
            return str(arguments[key])[:1000]
    return "task-scoped capability"


def _projected_effect(item: dict[str, Any]) -> dict[str, str]:
    if item.get("tool_name") == "run_command":
        return {
            "persistence": "sandbox_lifetime",
            "reversibility": "sandbox_disposable",
            "description": "命令在一次性 Kali 沙箱中执行；网络影响仅限任务授权目标。",
        }
    return {
        "persistence": "task_workspace" if item.get("tool_name") == "save_note" else "none",
        "reversibility": "reversible" if item.get("tool_name") == "save_note" else "not_applicable",
        "description": "任务范围内的受治理操作。",
    }


def _expected_outcome(tool_name: str) -> str:
    if tool_name == "run_command":
        return "在隔离 Kali 沙箱中执行一次命令，并将输出保存为可追溯 Artifact。"
    return f"执行一次 {tool_name} 并将结果返回当前 Worker。"


__all__ = ["event_projection", "runtime_snapshot_projection", "task_list_projection"]
