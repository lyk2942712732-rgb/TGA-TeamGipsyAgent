"""Stable projection from compact product state to the existing v2 frontend."""

from __future__ import annotations

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
    solvers = _solvers(raw.get("solver_runs", []), task_id)
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
    projected_status = "awaiting_user_input" if awaiting_user else status
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
    session = {
        "status": projected_status,
        "supervisor_solver_id": "supervisor" if solvers else None,
        "active_solver_count": sum(item["status"] == "running" for item in solvers),
        "max_active_workers": runtime.graph.max_active_workers if runtime else 1,
        "task_budget_usage": {
            "turns": model_calls,
            "model_calls": model_calls,
            "tool_calls": len(actions),
            "artifacts": len(artifacts),
        },
        "stop_reason": "user_input_required" if awaiting_user else None,
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
        "stop_reason": None,
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
            "task_entry_url": None,
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
            "task_common_skills": task["spec"].get("selected_skill_names"),
            "agent_prompt": None,
        },
        "task_common_skill_snapshot": {
            "selected": task["spec"].get("selected_skill_names"),
            "selection": "explicit"
            if task["spec"].get("selected_skill_names") is not None
            else "automatic",
        },
        "session": session,
        "team": team,
        "solvers": solvers,
        "intents": intents,
        "worker_results": [],
        "knowledge": [],
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


def _solvers(runs: list[dict[str, Any]], task_id: str) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        latest[run["solver_id"]] = run
    return [
        {
            "task_id": task_id,
            "solver_id": run["solver_id"],
            "definition_id": run["solver_id"],
            "orchestration_role": run["role"],
            "specialties": [],
            "parent_solver_id": None,
            "assigned_intent_id": run.get("intent_id"),
            "status": run["status"],
            "current_summary": run["summary"],
            "model_snapshot": {},
            "skill_snapshot": {},
            "capability_binding": {},
            "budget_usage": {
                "input_tokens": run["input_tokens"],
                "output_tokens": run["output_tokens"],
                "tool_calls": run["tool_calls"],
            },
            "timestamps": {
                "created_at": run["created_at"],
                "updated_at": run["updated_at"],
            },
        }
        for run in latest.values()
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
    return {
        "id": item["id"],
        "action_id": item["id"],
        "solver_id": item["solver_id"],
        "intent_id": item.get("intent_id"),
        "capability": item["tool_name"],
        "target": "",
        "risk": item["risk"],
        "effect": {},
        "arguments": item["arguments"],
        "status": item["status"],
        "summary": item["summary"],
        "artifact_ids": item["artifact_ids"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _approval(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "approval_id": item["id"],
        "solver_id": item["solver_id"],
        "intent_id": item.get("intent_id"),
        "action_id": item["id"],
        "action": {
            "id": item["id"],
            "capability": item["tool_name"],
            "status": "pending",
        },
        "risk": item["risk"],
        "effect": {},
        "reason": "Tool requires operator approval.",
        "alternatives": [],
        "deadline": "",
        "status": "pending",
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


__all__ = ["event_projection", "runtime_snapshot_projection", "task_list_projection"]
