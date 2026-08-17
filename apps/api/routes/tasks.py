"""Task, evidence and operations routes backed by TGA2."""

# FastAPI dependencies are intentionally declared in function defaults.
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from apps.api.dependencies import container
from tga2.bootstrap import Container
from tga2.config import Configuration
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
    payload: dict,
    staging: Path,
    configuration: Configuration,
    external_tool_names: set[str] | None = None,
) -> CreateTaskRequest:
    browser_input = payload.get("input") or {}
    paths = []
    for asset_id in browser_input.get("fileIds") or payload.get("input_paths") or []:
        matches = list(staging.glob(f"{asset_id}-*"))
        if matches:
            paths.append(str(matches[0]))
    execution = payload.get("executionPolicy") or payload.get("execution_policy") or {}
    mode = str(payload.get("mode") or "ctf")
    try:
        scene = configuration.scene(mode)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc
    mode_options = {
        **dict(scene.get("default_mode_config") or {}),
        **dict(payload.get("modeOptions") or payload.get("mode_options") or {}),
    }
    network = execution.get("network") or {}
    compute = execution.get("local_compute") or {}
    allowed = set((execution.get("tool") or {}).get("allowed_tools") or ())
    if not allowed:
        allowed = set(configuration.runtime.tool_defaults.allowed)
        if compute.get("mode") == "isolated":
            allowed.add("run_command")
    allowed.update(external_tool_names or ())
    approval_required = set(
        (execution.get("tool") or {}).get("approval_required") or ()
    )
    high_impact = execution.get("high_impact") or {}
    return CreateTaskRequest(
        id=payload.get("id"),
        name=str(payload.get("name") or "Untitled task"),
        objective=str(payload.get("goal") or payload.get("objective") or "").strip(),
        mode=mode,
        mode_options=mode_options,
        instructions=[str(browser_input.get("text"))]
        if browser_input.get("text")
        else list(payload.get("instructions") or []),
        constraints=list(payload.get("constraints") or []),
        success_criteria=list(payload.get("success_criteria") or []),
        input_paths=paths,
        execution_policy=ExecutionPolicy(
            tool=ToolPolicy(
                allowed_tools=frozenset(allowed),
                approval_required=frozenset(approval_required),
                denied_tools=frozenset(
                    (execution.get("tool") or {}).get("denied_tools") or ()
                ),
                max_tool_calls=configuration.runtime.budget.task.max_tool_calls,
            ),
            network_access=network.get("access", "disabled")
            if network.get("access") in {"disabled", "task_sources", "public_internet"}
            else "disabled",
            allowed_origins=tuple(
                network.get("custom_origins") or network.get("seed_origins") or ()
            ),
            local_compute=compute.get("mode", "disabled"),
            high_impact_mode=high_impact.get("mode", "forbidden")
            if high_impact.get("mode")
            in {"forbidden", "approval_required", "allowlisted"}
            else "forbidden",
            high_impact_allowed_actions=tuple(high_impact.get("allowed_actions") or ()),
            command_timeout_seconds=int(
                compute.get(
                    "timeout_seconds",
                    configuration.runtime.kali.command_timeout_seconds,
                )
            ),
        ),
    )


@router.get("/mode-profiles")
def mode_profiles(app: Container = Depends(container)):
    return {
        "schema_version": app.configuration.scenes.schema_version,
        "profiles": app.configuration.scenes.scenes,
    }


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
        app.configuration,
        {tool.name for tool in app.runtime.external_tools},
    )
    _validate_role_models(app)
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
        app.configuration,
        {tool.name for tool in app.runtime.external_tools},
    )
    _validate_role_models(app)
    packages = app.runtime.skills.list()
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
        "skill_catalog": {
            "strategy": "worker_on_demand",
            "package_count": len(packages),
            "content_sha256": hashlib.sha256(
                "".join(item.content_sha256 for item in packages).encode()
            ).hexdigest(),
        },
        "mcp_catalog_version": "langchain-mcp-adapters",
        "model_verification_id": hashlib.sha256(
            json.dumps(
                {
                    role: app.configuration.runtime.roles[role].model.model_dump()
                    for role in app.configuration.runtime.roles
                },
                sort_keys=True,
            ).encode()
        ).hexdigest(),
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
                yield f"id: {cursor}\nevent: event\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            if (
                app.runtime.snapshot(task_id)["task"]["status"]
                in {"completed", "completed_with_limitations", "failed", "cancelled"}
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
    byte_limit = app.configuration.runtime.files.artifact_preview_max_bytes
    return {
        "artifact": {"id": artifact_id, **item},
        "preview": raw[:byte_limit].decode("utf-8", errors="replace"),
        "truncated": len(raw) > byte_limit,
        "redactions": 0,
        "byte_limit": byte_limit,
        "download_url": f"/api/v2/tasks/{task_id}/artifacts/{artifact_id}?download=true",
    }


@router.post("/input-uploads", status_code=201)
async def stage_input(
    request: Request, filename: str, app: Container = Depends(container)
):
    raw = await request.body()
    byte_limit = app.configuration.runtime.files.upload_max_bytes
    if len(raw) > byte_limit:
        raise HTTPException(413, f"input exceeds configured limit ({byte_limit} bytes)")
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
            "mediaKind": (
                "image"
                if request.headers.get("content-type", "").startswith("image/")
                else "text"
                if request.headers.get("content-type", "").startswith("text/")
                else "document"
                if request.headers.get("content-type", "") in {"application/pdf", "application/json"}
                else "other"
            ),
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
    approved = (
        payload.get("approved")
        if "approved" in payload
        else payload.get("decision") == "approve"
    )
    return _call(
        app.runtime.decide_tool_action,
        task_id,
        action_id,
        approved=bool(approved),
        message=str(payload.get("reason") or ""),
    )


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


@router.post("/tasks/{task_id}/user-input")
def user_input(task_id: str, payload: dict, app: Container = Depends(container)):
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(422, "content is required")
    current = _call(app.runtime.snapshot, task_id)
    if current["session"]["status"] != "awaiting_user_input":
        raise HTTPException(409, "task is not waiting for user input")
    return _call(app.runtime.resume_task, task_id, {"content": content})


@router.post("/tasks/{task_id}/intents/{solver_id}/retry")
def graph_managed(task_id: str, solver_id: str):
    return {"task_id": task_id, "accepted": False, "status": "graph_managed"}


@router.post("/tasks/{task_id}/solvers/{solver_id}/messages")
def solver_message(
    task_id: str, solver_id: str, payload: dict, app: Container = Depends(container)
):
    attachments = []
    staging = (app.run_root / ".staging").resolve()
    for item in payload.get("attachments") or []:
        asset_id = str((item or {}).get("id") or "")
        if not asset_id or not re.fullmatch(r"[0-9a-f]{32}", asset_id):
            raise HTTPException(422, "invalid attachment id")
        matches = list(staging.glob(f"{asset_id}-*"))
        if len(matches) != 1:
            raise HTTPException(422, f"staged attachment not found: {asset_id}")
        path = matches[0].resolve()
        path.relative_to(staging)
        attachments.append(
            {
                "path": str(path),
                "name": Path(str((item or {}).get("originalName") or path.name)).name,
                "media_type": str((item or {}).get("mimeType") or "application/octet-stream"),
            }
        )
    return _call(
        app.runtime.send_solver_message,
        task_id,
        solver_id,
        content=str(payload.get("content") or ""),
        attachments=attachments,
    )


@router.post("/tasks/{task_id}/solvers/{solver_id}/control")
def solver_control(
    task_id: str, solver_id: str, payload: dict, app: Container = Depends(container)
):
    return _call(
        app.runtime.control_solver, task_id, solver_id, str(payload.get("action") or "")
    )


@router.put("/tasks/{task_id}/solvers/{solver_id}/model")
def solver_model(
    task_id: str, solver_id: str, payload: dict, app: Container = Depends(container)
):
    return _call(
        app.runtime.set_solver_model,
        task_id,
        solver_id,
        str(payload.get("provider_id") or ""),
        str(payload.get("model_id") or ""),
    )


@router.get("/dashboard")
def dashboard(app: Container = Depends(container)):
    values = app.runtime.list_tasks()
    active = [
        x
        for x in values
        if x["status"] in {"running", "awaiting_approval", "awaiting_user_input"}
    ]
    completed = [
        x for x in values if x["status"] in {"completed", "completed_with_limitations"}
    ]
    return {
        "view_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics": {
            "running_tasks": len(active),
            "pending_approvals": sum(
                int(x.get("pending_approvals", 0)) for x in values
            ),
            "active_solvers": sum(int(x.get("active_solvers", 0)) for x in values),
        },
        "needs_attention": [x for x in active if x.get("needs_attention")],
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


def _validate_role_models(app: Container) -> None:
    invalid = [
        role
        for role in app.configuration.runtime.roles
        if not app.configuration.role_model_status(role)["ready"]
    ]
    if invalid:
        raise HTTPException(
            409,
            {
                "code": "SOLVER_MODEL_CONFIGURATION_REQUIRED",
                "message": "Configure a verified model for every Solver in the Solver page.",
                "invalid": invalid,
            },
        )
