"""Runtime-derived catalogs; unsupported capabilities are reported honestly."""

# FastAPI dependencies are intentionally declared in function defaults.
# ruff: noqa: B008

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import container
from tga2.bootstrap import Container
from tga2.catalogs import (
    host_capabilities,
    kali_capabilities,
    kali_profiles,
    solver_definitions,
    team_templates,
)

router = APIRouter(tags=["catalog"])


def _solvers(app: Container):
    values = solver_definitions()
    for value in values:
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
    for item in items:
        item["enabled"] = bool(app.configuration.runtime.sandbox_image)
        item["image"] = app.configuration.runtime.sandbox_image or item["image"]
    return {"items": items, "total": len(items)}


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
    app.configuration.update_solver_tools(solver_id, sorted(current))
    kali = payload.get("kali")
    if kali and "kali.exec" in (kali.get("capabilities") or []):
        app.configuration.runtime = app.configuration.runtime.model_copy(
            update={"sandbox_image": "kalilinux/kali-rolling:latest"}
        )
        app.configuration.save()
    return solver(solver_id, app)


@router.get("/solvers/{solver_id}/kali-health")
@router.post("/solvers/{solver_id}/kali-health/check")
def solver_health(solver_id: str, app: Container = Depends(container)):
    solver(solver_id, app)
    return _health(solver_id, app, detail=True)


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
        items = [
            {"task_id": item["task_id"], "name": item["name"], "status": item["status"]}
            for item in app.runtime.list_tasks()
            if item["status"] == "completed"
        ]
    elif kind == "resources":
        items = [
            {
                "task_id": item["task_id"],
                "name": item["name"],
                "artifacts": item["artifacts"],
            }
            for item in app.runtime.list_tasks()
        ]
    elif kind == "policies":
        items = [
            {
                "id": "tga2-evidence-first",
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
            for mode in (
                "ctf",
                "code_audit",
                "penetration_test",
                "incident_response",
                "vulnerability_research",
                "reverse_analysis",
            )
        ]
    elif kind == "knowledge-bases":
        items = []
    else:
        raise HTTPException(404, "catalog not found")
    if query:
        items = [item for item in items if query.casefold() in str(item).casefold()]
    return {
        "supported": kind != "knowledge-bases",
        "reason": "TGA2 removed the unused knowledge-base subsystem."
        if kind == "knowledge-bases"
        else None,
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


def _health(solver_id: str, app: Container, detail: bool = False):
    requires = (
        solver_id == "worker"
        and "run_command" in app.configuration.runtime.solver_tools.get("worker", ())
    )
    ready = bool(app.configuration.runtime.sandbox_image)
    value = {
        "solver_id": solver_id,
        "requires_kali": requires,
        "profile_id": "tga2-kali" if requires else None,
        "status": "unknown"
        if requires and ready
        else "runtime_disabled"
        if requires
        else "host_only",
    }
    if detail:
        value.update(
            {
                "image": app.configuration.runtime.sandbox_image,
                "image_status": "configured" if ready else "disabled",
                "runtime_status": "not_probed",
                "checked_at": None,
                "reasons": [
                    {
                        "code": "NOT_PROBED",
                        "message": "Docker is probed only when a task invokes the shell.",
                    }
                ]
                if ready
                else [],
                "missing_executables": [],
                "image_store": {"status": "unknown", "error": None},
                "toolset": {
                    "expected_digest": None,
                    "actual_digest": None,
                    "status": "not_probed",
                },
            }
        )
    return value
