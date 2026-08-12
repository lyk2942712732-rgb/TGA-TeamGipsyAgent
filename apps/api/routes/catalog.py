"""Runtime-derived catalogs; unsupported capabilities are reported honestly."""

# FastAPI dependencies are intentionally declared in function defaults.
# ruff: noqa: B008

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from apps.api.dependencies import container
from tga2.agent.roles import DEFAULT_ROLE_PROMPTS
from tga2.bootstrap import Container
from tga2.catalogs import (
    MODES,
    host_capabilities,
    kali_capabilities,
    kali_profiles,
    solver_definitions,
    team_templates,
)
from tga2.config import KALI_PROFILE_ID, KaliSandboxSettings

router = APIRouter(tags=["catalog"])


def _solvers(app: Container):
    values = solver_definitions()
    for value in values:
        role = value["role"]
        common = app.configuration.runtime.prompts.get("common", "").strip()
        role_prompt = app.configuration.runtime.prompts.get(role, "").strip()
        value["system_prompt_template"] = "\n\n".join(
            item
            for item in (common, role_prompt or DEFAULT_ROLE_PROMPTS[role])
            if item
        )
        names = app.configuration.runtime.solver_tools.get(value["id"], [])
        value["host_capabilities"] = [
            {
                "id": item["id"],
                "display_name": item["display_name"],
                "category": item["category"],
                "risk": item["risk"],
                "source": "host",
            }
            for item in host_capabilities()
            if item["id"] in names
        ]
        value["host_capability_overrides"] = {"add": names, "remove": []}
        if role == "worker" and "run_command" in names:
            kali = app.configuration.runtime.kali
            image_name, image_tag = _image_parts(kali.image)
            value["kali"] = {
                "profile_id": kali.profile_id,
                "capabilities": ["kali.exec"],
                "image_name": image_name,
                "image_tag": image_tag,
                "image_digest": kali.expected_digest,
                "allowed_executables": [],
                "session_executables": [],
                "network_mode": "task_policy",
                "limits": {
                    "cpu_cores": 1,
                    "memory_mb": 1024,
                    "timeout_seconds": 120,
                    "max_processes": 256,
                },
                "tools": [],
            }
        value["content_sha256"] = hashlib.sha256(
            json.dumps(
                {
                    "role": role,
                    "prompt": value["system_prompt_template"],
                    "tools": names,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    return values


@router.get("/capabilities")
def capabilities(app: Container = Depends(container)):
    host = host_capabilities()
    external = [
        {
            "id": tool.name,
            "display_name": tool.name,
            "category": "mcp",
            "description": tool.description,
            "allowed_roles": ["worker"],
            "risk": (tool.metadata or {}).get("tga2_risk", "active"),
            "input_schema": tool.args_schema.model_json_schema()
            if hasattr(tool.args_schema, "model_json_schema")
            else {},
            "output_schema": {"type": "string"},
            "handler_key": tool.name,
            "handler_status": "ready",
            "assigned_solver_count": 1,
            "assigned_solver_ids": ["worker"],
        }
        for tool in app.runtime.external_tools
    ]
    return {
        "host": [*host, *external],
        "kali": kali_capabilities(),
        "capabilities": [
            {
                "name": item["id"],
                "availability": "available",
                "risk": item["risk"],
                "modes": [],
            }
            for item in [*host, *external]
        ],
        "tools": {
            "availability": "available" if external else "unavailable",
            "reason": None if external else "No enabled MCP tools are reachable.",
            "tools": [
                {
                    "tool_id": item["id"],
                    "provider_name": "mcp",
                    "risk": item["risk"],
                    "modes": [],
                    "methods": [
                        {
                            "name": item["id"],
                            "description": item["description"],
                            "input_schema": item["input_schema"],
                        }
                    ],
                }
                for item in external
            ],
        },
    }


@router.get("/capabilities/host")
def host(app: Container = Depends(container)):
    return {"items": capabilities(app)["host"], "total": len(capabilities(app)["host"])}


@router.get("/capabilities/host-profiles")
def host_profiles():
    items = [
        {
            "id": f"{role}-default",
            "capability_ids": [
                item["id"]
                for item in host_capabilities()
                if role in item["allowed_roles"]
            ],
        }
        for role in ("supervisor", "worker", "reviewer", "reporter")
    ]
    return {"items": items, "total": len(items)}


@router.get("/capabilities/kali")
def kali():
    items = kali_capabilities()
    return {"items": items, "total": len(items)}


@router.get("/kali/profiles")
def kali_profile_list(app: Container = Depends(container)):
    items = kali_profiles()
    settings = app.configuration.runtime.kali
    for item in items:
        image_name, image_tag = _image_parts(settings.image)
        item.update(
            {
                "id": settings.profile_id,
                "enabled": settings.enabled,
                "image": settings.image,
                "image_name": image_name,
                "image_tag": image_tag,
                "image_digest": settings.expected_digest,
            }
        )
        item["config_sha256"] = hashlib.sha256(
            json.dumps(item, sort_keys=True, default=str).encode()
        ).hexdigest()
    return {"items": items, "total": len(items)}


@router.put("/kali/profiles/{profile_id}")
def update_kali_profile(
    profile_id: str, payload: dict, app: Container = Depends(container)
):
    if profile_id != KALI_PROFILE_ID:
        raise HTTPException(404, "Kali profile not found")
    current = app.configuration.runtime.kali
    try:
        settings = KaliSandboxSettings.model_validate(
            {
                "profile_id": profile_id,
                "enabled": payload.get("enabled", current.enabled),
                "image": str(payload.get("image", current.image)).strip(),
                "expected_digest": payload.get(
                    "expected_digest", current.expected_digest
                )
                or None,
            }
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc
    if settings.enabled and not settings.image:
        raise HTTPException(422, "enabled Kali profile requires an image")
    app.configuration.update_kali(settings)
    return kali_profile_list(app)["items"][0]


@router.get("/solvers")
def solvers(app: Container = Depends(container)):
    items = _solvers(app)
    return {"items": items, "total": len(items)}


@router.get("/solvers/kali-health")
def all_solver_health(app: Container = Depends(container)):
    items = [_health(item["id"], app) for item in _solvers(app)]
    return {"items": items, "total": len(items)}


@router.get("/solvers/{solver_id}")
def solver(solver_id: str, app: Container = Depends(container)):
    value = next((item for item in _solvers(app) if item["id"] == solver_id), None)
    if value is None:
        raise HTTPException(404, "solver not found")
    return value


@router.get("/solvers/{solver_id}/manifest-preview")
def manifest(solver_id: str, app: Container = Depends(container)):
    return solver(solver_id, app)


@router.put("/solvers/{solver_id}/capabilities")
def update_solver(solver_id: str, payload: dict, app: Container = Depends(container)):
    solver(solver_id, app)
    overrides = payload.get("host_capability_overrides") or {}
    current = set(app.configuration.runtime.solver_tools.get(solver_id, ()))
    current.update(overrides.get("add") or ())
    current.difference_update(overrides.get("remove") or ())
    valid = {item["id"] for item in host_capabilities()} | {
        tool.name for tool in app.runtime.external_tools
    }
    unknown = current - valid
    if unknown:
        raise HTTPException(422, f"unknown capabilities: {sorted(unknown)}")
    kali = payload.get("kali")
    if kali:
        if kali.get("profile_id") != app.configuration.runtime.kali.profile_id:
            raise HTTPException(422, "unknown Kali profile")
        kali_capabilities = set(kali.get("capabilities") or ())
        if kali_capabilities - {"kali.exec"}:
            raise HTTPException(422, "unsupported Kali capability")
        if "kali.exec" in kali_capabilities:
            current.add("run_command")
        else:
            current.discard("run_command")
    else:
        current.discard("run_command")
    app.configuration.update_solver_tools(solver_id, sorted(current))
    return solver(solver_id, app)


@router.get("/solvers/{solver_id}/kali-health")
@router.post("/solvers/{solver_id}/kali-health/check")
def solver_health(solver_id: str, app: Container = Depends(container)):
    solver(solver_id, app)
    return _health(solver_id, app, detail=True, probe=True)


@router.get("/tools/health")
def tools(app: Container = Depends(container)):
    records = []
    for server, config in app.mcp.list().items():
        loaded = [
            tool
            for tool in app.runtime.external_tools
            if tool.name.startswith(f"{server}_")
        ]
        stdio = config.get("stdio") or {}
        http = config.get("http") or {}
        records.append(
            {
                "server": server,
                "status": "ready" if loaded else "unavailable",
                "configured": True,
                "enabled": config.get("enabled", True),
                "reachable": bool(loaded),
                "discovered": bool(loaded),
                "tools": len(loaded),
                "transport": config.get("transport", "stdio"),
                "image": stdio.get("image"),
                "endpoint": http.get("url"),
                "error": app.mcp_errors.get(server),
            }
        )
    return {
        "configured": bool(app.mcp.list()),
        "status": "error" if app.mcp_error else "available",
        "records": records,
        "last_error": app.mcp_error,
    }


@router.get("/catalog/{kind}")
def catalog(
    kind: str, query: str = "", limit: int = 100, app: Container = Depends(container)
):
    if kind == "teams":
        items = team_templates()
    elif kind == "solvers":
        items = _solvers(app)
    elif kind == "skills":
        items = [
            {"name": item.name, "summary": item.description}
            for item in app.runtime.skills.list()
        ]
    elif kind == "reports":
        items = []
        for task in app.runtime.list_tasks():
            report = app.runtime.report(task["task_id"])
            if report is None:
                continue
            items.append(
                {
                    "id": f"report-{task['task_id']}",
                    "task_id": task["task_id"],
                    "task_name": task["name"],
                    "title": f"{task['name']} 报告",
                    "mode": task["mode"],
                    "status": "final",
                    "findings": task["findings"],
                    "updated_at": report["created_at"],
                }
            )
    elif kind == "resources":
        items = []
        for task in app.runtime.list_tasks():
            snapshot = app.runtime.snapshot(task["task_id"])
            for artifact in snapshot["artifacts"]:
                items.append(
                    {
                        "id": artifact["artifact_id"],
                        "task_id": task["task_id"],
                        "task_name": task["name"],
                        "kind": "artifacts",
                        "title": artifact.get("path") or artifact["artifact_id"],
                        "status": "available",
                        "raw": artifact,
                    }
                )
            for claim in snapshot["evidence_claims"]:
                items.append(
                    {
                        "id": claim["claim_id"],
                        "task_id": task["task_id"],
                        "task_name": task["name"],
                        "kind": "evidence",
                        "title": claim["statement_preview"],
                        "status": claim["status"],
                        "raw": claim,
                    }
                )
            for finding in snapshot["findings"]:
                items.append(
                    {
                        "id": finding["finding_id"],
                        "task_id": task["task_id"],
                        "task_name": task["name"],
                        "kind": "findings",
                        "title": finding["title"],
                        "status": finding["status"],
                        "raw": finding,
                    }
                )
    elif kind == "policies":
        items = [
            {
                "id": f"tga2-evidence-first-{mode}",
                "type": "execution",
                "mode": mode,
                "mode_label": mode.replace("_", " ").title(),
                "preset": "offline_analysis",
                "status": "active",
                "source": "tga2",
                "editable": False,
                "execution_policy": {
                    "preset": "offline_analysis",
                    "network": {
                        "access": "disabled",
                        "interaction": "observe",
                        "seed_origins": [],
                        "custom_origins": [],
                        "custom_domains": [],
                        "custom_cidrs": [],
                        "custom_ports": [],
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
            }
            for mode in MODES
        ]
    else:
        raise HTTPException(404, "catalog not found")
    if query:
        items = [item for item in items if query.casefold() in str(item).casefold()]
    return {
        "supported": True,
        "reason": None,
        "kind": kind,
        "items": items[:limit],
        "total": len(items),
    }


@router.get("/system/readiness")
def readiness(app: Container = Depends(container)):
    return {
        "status": "degraded" if app.mcp_error else "ready",
        "runtime": "tga2",
        "storage": {"writable": True},
        "graph": {"framework": "langgraph", "available": True},
        "mcp": {"error": app.mcp_error},
    }


def _health(
    solver_id: str,
    app: Container,
    detail: bool = False,
    probe: bool = False,
):
    requires = (
        solver_id == "worker"
        and "run_command" in app.configuration.runtime.solver_tools.get("worker", ())
    )
    kali = app.configuration.runtime.kali
    image = kali.image if kali.enabled else None
    ready = bool(image)
    docker = shutil.which("docker") if probe and ready else None
    image_ready = False
    digest_verified = False
    actual_digest = None
    probe_error = None
    if docker:
        try:
            result = subprocess.run(
                [docker, "image", "inspect", image],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            image_ready = result.returncode == 0
            if image_ready:
                inspected = json.loads(result.stdout)[0]
                actual_digest = inspected.get("Id")
                candidates = {actual_digest, *(inspected.get("RepoDigests") or [])}
                digest_verified = not kali.expected_digest or any(
                    candidate == kali.expected_digest
                    or str(candidate).endswith(f"@{kali.expected_digest}")
                    for candidate in candidates
                    if candidate
                )
            else:
                probe_error = (result.stderr or result.stdout).strip()[:1000]
        except (OSError, subprocess.TimeoutExpired) as exc:
            probe_error = str(exc)
    elif probe and ready:
        probe_error = "Docker executable was not found."
    value = {
        "solver_id": solver_id,
        "requires_kali": requires,
        "profile_id": kali.profile_id if requires else None,
        "status": "healthy"
        if requires and ready and probe and image_ready and digest_verified
        else "image_unverified"
        if requires and ready and probe and image_ready
        else "runtime_unavailable"
        if requires and ready and probe
        else "unknown"
        if requires and ready
        else "runtime_disabled"
        if requires
        else "host_only",
    }
    if detail:
        value.update(
            {
                "image": image,
                "image_status": "healthy"
                if image_ready and digest_verified
                else "image_unverified"
                if image_ready
                else "configured"
                if ready
                else "disabled",
                "runtime_status": "docker_sandbox_available"
                if image_ready
                else "docker_sandbox_unavailable"
                if probe and ready
                else "disabled"
                if not ready
                else "not_probed",
                "checked_at": datetime.now(UTC).isoformat() if probe else None,
                "reasons": [
                    {
                        "code": "IMAGE_DIGEST_MISMATCH"
                        if image_ready and not digest_verified
                        else "DOCKER_PROBE_FAILED"
                        if probe
                        else "NOT_PROBED",
                        "message": (
                            f"Expected {kali.expected_digest}, found {actual_digest}."
                            if image_ready and not digest_verified
                            else probe_error
                            or "Use the health-check endpoint to probe Docker and the image."
                        ),
                    }
                ]
                if ready and (not image_ready or not digest_verified)
                else [],
                "missing_executables": [],
                "image_store": {
                    "status": "readable"
                    if image_ready
                    else "unreadable"
                    if probe and ready
                    else "unknown",
                    "error": probe_error,
                    "expected_digest": kali.expected_digest,
                    "actual_digest": actual_digest,
                },
                "toolset": {
                    "expected_digest": None,
                    "actual_digest": None,
                    "status": "not_applicable",
                },
            }
        )
    return value


def _image_parts(image: str) -> tuple[str, str]:
    without_digest = image.split("@", 1)[0]
    slash = without_digest.rfind("/")
    colon = without_digest.rfind(":")
    if colon > slash:
        return without_digest[:colon], without_digest[colon + 1 :]
    return without_digest, "latest"
