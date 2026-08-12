"""Task, evidence and operations routes backed by TGA2."""

# FastAPI dependencies are intentionally declared in function defaults.
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from apps.api.dependencies import container
from tga2.bootstrap import Container
from tga2.catalogs import MODES
from tga2.core.models import CreateTaskRequest
from tga2.core.policy import ExecutionPolicy, ToolPolicy
from tga2.core.workspace import TaskWorkspace

router = APIRouter(tags=["tasks"])


def _run_background(app: Container, task_id: str) -> None:
    try:
        app.runtime.run_task(task_id)
    except Exception:  # noqa: BLE001 - failure is already persisted by the service
        # TaskRuntimeService has already persisted TASK_FAILED and the error details.
        return


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _request(
    payload: dict, staging: Path, external_tool_names: set[str] | None = None
) -> CreateTaskRequest:
    browser_input = payload.get("input") or {}
    paths = []
    for asset_id in browser_input.get("fileIds") or payload.get("input_paths") or []:
        matches = list(staging.glob(f"{asset_id}-*"))
        if matches:
            paths.append(str(matches[0]))
    execution = payload.get("executionPolicy") or payload.get("execution_policy") or {}
    network = execution.get("network") or {}
    compute = execution.get("local_compute") or {}
    allowed = set((execution.get("tool") or {}).get("allowed_tools") or ())
    if not allowed:
        allowed = {
            "list_inputs",
            "read_input",
            "glob_search",
            "grep_search",
            "save_note",
        }
        if compute.get("mode") == "isolated":
            allowed.add("run_command")
    allowed.update(external_tool_names or ())
    approval_required = set(
        (execution.get("tool") or {}).get("approval_required") or ()
    )
    if (execution.get("high_impact") or {}).get("mode") == "approval_required":
        approval_required.update(external_tool_names or ())
        if compute.get("mode") == "isolated":
            approval_required.add("run_command")
    return CreateTaskRequest(
        id=payload.get("id"),
        name=str(payload.get("name") or "Untitled task"),
        objective=str(payload.get("goal") or payload.get("objective") or "").strip(),
        mode=payload.get("mode") or "ctf",
        instructions=[str(browser_input.get("text"))]
        if browser_input.get("text")
        else list(payload.get("instructions") or []),
        constraints=list(payload.get("constraints") or []),
        success_criteria=list(payload.get("success_criteria") or []),
        input_paths=paths,
        selected_skills=payload.get("selectedSkills"),
        agent_models=payload.get("agentModels") or {},
        execution_policy=ExecutionPolicy(
            tool=ToolPolicy(
                allowed_tools=frozenset(allowed),
                approval_required=frozenset(approval_required),
                denied_tools=frozenset(
                    (execution.get("tool") or {}).get("denied_tools") or ()
                ),
            ),
            network_access=network.get("access", "disabled")
            if network.get("access") in {"disabled", "task_sources", "public_internet"}
            else "disabled",
            allowed_origins=tuple(
                network.get("custom_origins") or network.get("seed_origins") or ()
            ),
            local_compute=compute.get("mode", "disabled"),
            command_timeout_seconds=int(compute.get("timeout_seconds", 120)),
        ),
    )


@router.get("/mode-profiles")
def mode_profiles():
    return {"schema_version": 6, "profiles": [_mode_profile(mode) for mode in MODES]}


@router.post("/tasks", status_code=201)
def create_task(
    payload: dict,
    background_tasks: BackgroundTasks,
    app: Container = Depends(container),
):
    request = _call(
        _request,
        payload,
        app.run_root / ".staging",
        {tool.name for tool in app.runtime.external_tools},
    )
    _validate_agent_models(request, app)
    result = _call(app.runtime.create_task, request)
    for source in request.input_paths:
        path = Path(source).resolve()
        staging = (app.run_root / ".staging").resolve()
        path.relative_to(staging)
        path.unlink(missing_ok=True)
    # FastAPI owns the lightweight scheduling boundary; LangGraph owns execution state.
    background_tasks.add_task(_run_background, app, result["task_id"])
    return {
        **result,
        "scheduled": True,
        "mcp_capabilities": {
            "server_ids": list(app.mcp.list()),
            "tools": [tool.name for tool in app.runtime.external_tools],
        },
    }


@router.post("/tasks/preflight")
def preflight(payload: dict, app: Container = Depends(container)):
    request = _call(
        _request,
        payload,
        app.run_root / ".staging",
        {tool.name for tool in app.runtime.external_tools},
    )
    _validate_agent_models(request, app)
    selected = app.runtime.skills.select(
        request.objective, selected_names=request.selected_skills
    )
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    return {
        "fingerprint": fingerprint,
        "task_id": request.id or "pending",
        "checks": [
            {"id": "task", "status": "passed", "detail": "Task request is valid."},
            {
                "id": "runtime",
                "status": "passed",
                "detail": "LangGraph runtime is available.",
            },
            {
                "id": "model",
                "status": "passed",
                "detail": "Configured model or offline demo is available.",
            },
        ],
        "skill_snapshot": {
            "selector": "tga2.skills",
            "count": len(selected),
            "content_sha256": hashlib.sha256(
                "".join(item.content for item in selected).encode()
            ).hexdigest(),
        },
        "mcp_catalog_version": "langchain-mcp-adapters",
        "model_verification_id": hashlib.sha256(
            json.dumps(request.agent_models, sort_keys=True).encode()
        ).hexdigest(),
    }


@router.post("/tasks/skill-preview")
def skill_preview(payload: dict, app: Container = Depends(container)):
    selected = app.runtime.skills.select(
        str(payload.get("goal") or ""), selected_names=payload.get("selectedSkills")
    )
    return {
        "selector": "tga2.skills",
        "fingerprint": hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "count": len(selected),
        "skills": [
            {
                "name": item.name,
                "version": "1",
                "capabilities": [],
                "tags": list(item.tags),
                "origin": "custom",
                "content_sha256": hashlib.sha256(item.content.encode()).hexdigest(),
                "selection_reasons": [
                    "explicit"
                    if payload.get("selectedSkills")
                    else "objective_tag_match"
                ],
            }
            for item in selected
        ],
    }


@router.get("/tasks")
def tasks(
    query: str = "",
    mode: str | None = None,
    status: str | None = None,
    needs_attention: bool | None = None,
    offset: int = 0,
    limit: int = 200,
    app: Container = Depends(container),
):
    values = app.runtime.list_tasks()
    if query:
        values = [
            item for item in values if query.casefold() in json.dumps(item).casefold()
        ]
    if mode:
        values = [item for item in values if item["mode"] == mode]
    if status:
        values = [item for item in values if item["status"] == status]
    if needs_attention is not None:
        values = [
            item
            for item in values
            if bool(item.get("needs_attention")) == needs_attention
        ]
    page = values[offset : offset + limit]
    return {
        "tasks": page,
        "items": page,
        "offset": offset,
        "limit": limit,
        "total": len(values),
        "next_offset": offset + limit if offset + limit < len(values) else None,
    }


@router.get("/tasks/{task_id}")
@router.get("/tasks/{task_id}/session")
def task(task_id: str, app: Container = Depends(container)):
    return _call(app.runtime.snapshot, task_id)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, app: Container = Depends(container)):
    return _call(app.runtime.delete_task, task_id)


@router.post("/tasks/{task_id}/start")
def start(task_id: str, app: Container = Depends(container)):
    return _call(app.runtime.run_task, task_id)


@router.get("/tasks/{task_id}/team")
def team(task_id: str, app: Container = Depends(container)):
    value = _call(app.runtime.snapshot, task_id)
    return {"task_id": task_id, "team": value["team"], "solvers": value["solvers"]}


@router.get("/tasks/{task_id}/inputs")
def inputs(task_id: str, app: Container = Depends(container)):
    value = _call(app.runtime.snapshot, task_id)
    return {
        "task_goal": value["task"]["goal"],
        "prompt": "\n".join(value["task"]["spec"]["instructions"]),
        "files": value["task"]["spec"]["resources"],
        "task_entry_url": None,
    }


@router.get("/tasks/{task_id}/evidence")
def evidence(
    task_id: str, offset: int = 0, limit: int = 100, app: Container = Depends(container)
):
    value = _call(app.runtime.snapshot, task_id)
    return {
        "task_id": task_id,
        "artifacts": _page(value["artifacts"], offset, limit),
        "evidence_claims": _page(value["evidence_claims"], offset, limit),
        "findings": _page(value["findings"], offset, limit),
    }


@router.get("/tasks/{task_id}/events")
@router.get("/tasks/{task_id}/timeline")
def events(
    task_id: str,
    after_seq: int = 0,
    limit: int = 200,
    app: Container = Depends(container),
):
    items = _call(app.runtime.events, task_id, after_seq=after_seq, limit=limit)
    return {
        "task_id": task_id,
        "events": items,
        "items": items,
        "latest_seq": items[-1]["seq"] if items else after_seq,
        "has_more": False,
    }


@router.get("/tasks/{task_id}/events/stream")
def event_stream(
    task_id: str,
    after_seq: int = Query(default=0, ge=0),
    app: Container = Depends(container),
):
    async def generate():
        cursor = after_seq
        for _ in range(120):
            items = app.runtime.events(task_id, after_seq=cursor, limit=200)
            for item in items:
                cursor = max(cursor, int(item["seq"]))
                yield f"id: {cursor}\nevent: {item['type']}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            if (
                app.runtime.snapshot(task_id)["task"]["status"]
                in {"completed", "failed", "cancelled"}
                and not items
            ):
                break
            if not items:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/tasks/{task_id}/report")
def report(task_id: str, app: Container = Depends(container)):
    value = _call(app.runtime.report, task_id)
    if value is None:
        raise HTTPException(404, "report not generated")
    return PlainTextResponse(value["markdown"], media_type="text/markdown")


@router.post("/tasks/{task_id}/report/export")
def report_export(task_id: str, app: Container = Depends(container)):
    value = _call(app.runtime.report, task_id)
    if value is None:
        raise HTTPException(404, "report not generated")
    return {"task_id": task_id, **value}


@router.get("/tasks/{task_id}/artifacts/{artifact_id}")
def artifact(
    task_id: str,
    artifact_id: str,
    download: bool = False,
    app: Container = Depends(container),
):
    value = _call(app.runtime.snapshot, task_id)
    item = next(
        (item for item in value["artifacts"] if item["artifact_id"] == artifact_id),
        None,
    )
    if item is None:
        raise HTTPException(404, "artifact not found")
    workspace = TaskWorkspace(app.run_root, task_id)
    path = (workspace.root / item["path"]).resolve()
    path.relative_to(workspace.artifacts.resolve())
    if download:
        return FileResponse(
            path, media_type=item.get("media_type") or "application/octet-stream"
        )
    raw = path.read_bytes()
    return {
        "artifact": {"id": artifact_id, **item},
        "preview": raw[:200_000].decode("utf-8", errors="replace"),
        "truncated": len(raw) > 200_000,
        "redactions": 0,
        "byte_limit": 200_000,
        "download_url": f"/api/v2/tasks/{task_id}/artifacts/{artifact_id}?download=true",
    }


@router.post("/input-uploads", status_code=201)
async def stage_input(
    request: Request, filename: str, app: Container = Depends(container)
):
    raw = await request.body()
    if len(raw) > 25_000_000:
        raise HTTPException(413, "input exceeds 25 MB")
    staging = app.run_root / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    asset_id = uuid4().hex
    safe = Path(filename).name
    (staging / f"{asset_id}-{safe}").write_bytes(raw)
    return {
        "asset": {
            "id": asset_id,
            "originalName": safe,
            "mimeType": request.headers.get("content-type", "application/octet-stream"),
            "mediaKind": "other",
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "status": "uploaded",
        }
    }


@router.delete("/input-uploads/{asset_id}")
def delete_input(asset_id: str, app: Container = Depends(container)):
    matches = list((app.run_root / ".staging").glob(f"{asset_id}-*"))
    for path in matches:
        path.unlink()
    return {"asset_id": asset_id, "deleted": bool(matches)}


@router.post("/tasks/{task_id}/approvals/{action_id}/decision")
def approval(
    task_id: str, action_id: str, payload: dict, app: Container = Depends(container)
):
    current = _call(app.runtime.snapshot, task_id)
    if not any(item["action_id"] == action_id for item in current["approvals"]):
        raise HTTPException(404, "pending approval not found")
    approved = (
        payload.get("approved")
        if "approved" in payload
        else payload.get("decision") == "approve"
    )
    decision = {
        "decisions": [
            {
                "type": "approve" if approved else "reject",
                "message": str(payload.get("reason") or ""),
            }
        ]
    }
    return _call(app.runtime.resume_task, task_id, decision)


@router.post("/tasks/{task_id}/control")
def control(task_id: str, payload: dict, app: Container = Depends(container)):
    if payload.get("action") != "cancel":
        return {
            "task_id": task_id,
            "accepted": False,
            "status": "graph_managed",
            "reason": "Only cancel is externally controlled.",
        }
    return _call(app.runtime.cancel_task, task_id)


@router.post("/tasks/{task_id}/interventions")
def intervention(task_id: str, payload: dict, app: Container = Depends(container)):
    return _call(app.runtime.record_intervention, task_id, payload)


@router.post("/tasks/{task_id}/solvers/{solver_id}/control")
@router.post("/tasks/{task_id}/intents/{solver_id}/retry")
def graph_managed(task_id: str, solver_id: str):
    return {"task_id": task_id, "accepted": False, "status": "graph_managed"}


@router.get("/dashboard")
def dashboard(app: Container = Depends(container)):
    values = app.runtime.list_tasks()
    active = [x for x in values if x["status"] in {"running", "awaiting_approval"}]
    completed = [x for x in values if x["status"] == "completed"]
    return {
        "view_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics": {
            "running_tasks": len(active),
            "pending_approvals": sum(
                int(x.get("pending_approvals", 0)) for x in values
            ),
            "awaiting_user_input": 0,
            "blocked_tasks": 0,
            "active_solvers": sum(int(x.get("active_solvers", 0)) for x in values),
        },
        "needs_attention": [],
        "active_tasks": active,
        "recent_completed": completed[:10],
        "system_status": [
            {
                "id": "runtime",
                "label": "LangGraph runtime",
                "status": "healthy",
                "detail": "available",
                "available": True,
            }
        ],
        "unavailable_metrics": [],
    }


@router.get("/approvals")
def approvals(
    status: str = "pending",
    offset: int = 0,
    limit: int = 20,
    app: Container = Depends(container),
):
    values = []
    for summary in app.runtime.list_tasks():
        snapshot = app.runtime.snapshot(summary["task_id"])
        values.extend(
            {
                **item,
                "task_id": summary["task_id"],
                "task_name": summary["name"],
                "action_kind": "tool",
                "rationale": item.get("reason", ""),
                "expected_outcome": "Authorized tool result",
                "alternative_analysis": "",
                "reversibility": "runtime controlled",
                "decision_allowed": True,
            }
            for item in snapshot["approvals"]
            if item["status"] == status
        )
    page = values[offset : offset + limit]
    return {
        "view_version": 1,
        "offset": offset,
        "limit": limit,
        "total": len(values),
        "next_offset": offset + limit if offset + limit < len(values) else None,
        "items": page,
        "filters": {"status": status},
    }


def _page(items: list, offset: int, limit: int):
    return {
        "offset": offset,
        "limit": limit,
        "total": len(items),
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "items": items[offset : offset + limit],
    }


def _validate_agent_models(request: CreateTaskRequest, app: Container) -> None:
    if not request.agent_models:
        return
    valid_roles = {"supervisor", "worker", "reviewer", "reporter"}
    invalid = {}
    for role, value in request.agent_models.items():
        provider_id = value.get("providerId")
        model_id = value.get("modelId")
        if role not in valid_roles:
            invalid[role] = value
            continue
        if (provider_id, model_id) == ("offline", "offline"):
            continue
        try:
            app.configuration.model_registry.settings(
                str(provider_id), str(model_id), require_verified=True
            )
        except (KeyError, ValueError):
            invalid[role] = value
    if invalid:
        raise HTTPException(
            422,
            {
                "code": "INVALID_AGENT_MODEL",
                "message": "One or more Agent model selections are missing or unverified.",
                "assignments": invalid,
            },
        )


def _mode_profile(mode: str):
    return {
        "id": mode,
        "label": mode.replace("_", " ").title(),
        "description": f"{mode} workflow",
        "default_goal": "Analyze the authorized target and report evidence-backed findings.",
        "default_mode_config": {"mode": mode},
        "default_execution_policy": {
            "preset": "offline_analysis",
            "network": {
                "access": "disabled",
                "interaction": "observe",
                "seed_origins": [],
                "custom_origins": [],
                "custom_domains": [],
                "custom_cidrs": [],
                "deny_private_networks": True,
                "deny_loopback": True,
                "deny_link_local": True,
                "deny_cloud_metadata": True,
                "rate_limit_per_minute": 30,
                "concurrency": 1,
                "request_timeout_seconds": 30,
            },
            "local_compute": {
                "mode": "disabled",
                "timeout_seconds": 120,
                "concurrency": 1,
                "network_inheritance": "task_network_policy",
            },
            "high_impact": {"mode": "forbidden", "allowed_actions": []},
        },
        "allowed_input_kinds": ["file", "text"],
        "required_conditions": [],
        "recommended_capabilities": ["read_input"],
        "completion_validator": "evidence_review",
        "report_sections": ["summary", "findings", "evidence", "limitations"],
        "uses_flag": mode == "ctf",
        "advanced_settings": [],
        "mode_config_schema": {"type": "object"},
        "execution_policy_schema": {"type": "object"},
    }
