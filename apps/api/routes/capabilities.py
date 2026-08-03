"""Authoritative Host and Kali capability management projections."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from apps.api.routes.support import _catalog_runner
from tga.application.capabilities import CapabilityAssignmentService
from tga.capabilities.mcp import health_snapshot


router = APIRouter(tags=["capabilities"])


def _assignments() -> CapabilityAssignmentService:
    return CapabilityAssignmentService()


def _host_payload(capability_id: str) -> dict[str, Any]:
    assignments = _assignments()
    try:
        item = assignments.host_registry.require(capability_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Host capability not found") from exc
    solver_ids = assignments.solvers_for_host(item.id)
    return {
        **item.model_dump(mode="json"),
        "handler_status": "ready",
        "assigned_solver_count": len(solver_ids),
        "assigned_solver_ids": list(solver_ids),
        "usage": {"calls": None, "failures": None, "last_called_at": None},
    }


@router.get("/capabilities")
def capability_summary() -> dict[str, Any]:
    """Compact current contract retained for system health only."""
    assignments = _assignments()
    return {
        "host": [_host_payload(item.id) for item in assignments.host_registry.all()],
        "kali": _kali_items(assignments),
    }


@router.get("/capabilities/host")
def host_capabilities() -> dict[str, Any]:
    assignments = _assignments()
    items = [_host_payload(item.id) for item in assignments.host_registry.all()]
    return {"items": items, "total": len(items)}


@router.get("/capabilities/host/{capability_id}")
def host_capability(capability_id: str) -> dict[str, Any]:
    return _host_payload(capability_id)


@router.get("/capabilities/host/{capability_id}/solvers")
def host_capability_solvers(capability_id: str) -> dict[str, Any]:
    assignments = _assignments()
    try:
        values = assignments.solvers_for_host(capability_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Host capability not found") from exc
    return {"capability_id": capability_id, "solver_ids": list(values), "total": len(values)}


def _kali_items(assignments: CapabilityAssignmentService) -> list[dict[str, Any]]:
    definitions = {
        "kali.exec": (
            "Kali command execution",
            "Execute one non-interactive allowlisted program in the assigned Kali Profile.",
        ),
        "kali.session": (
            "Kali interactive session",
            "Open and control an interactive PTY bound to the current SolverRun.",
        ),
    }
    values = []
    for capability_id, (display_name, description) in definitions.items():
        solver_ids = assignments.solvers_for_kali_capability(capability_id)
        profile_ids = sorted({
            definition.kali.profile_id
            for definition in assignments.definitions.all()
            if definition.kali is not None
            and capability_id in definition.kali.capabilities
        })
        values.append({
            "id": capability_id,
            "display_name": display_name,
            "description": description,
            "risk": "active",
            "input_schema": assignments.kali_schema(capability_id),
            "assigned_solver_count": len(solver_ids),
            "assigned_solver_ids": list(solver_ids),
            "profile_ids": profile_ids,
            "usage": {"calls": None, "failures": None, "last_called_at": None},
        })
    return values


@router.get("/capabilities/kali")
def kali_capabilities() -> dict[str, Any]:
    items = _kali_items(_assignments())
    return {"items": items, "total": len(items)}


@router.get("/tools/health")
def tool_health() -> dict[str, Any]:
    runner = _catalog_runner()
    snapshot = health_snapshot(runner)
    snapshot["configured"] = runner is not None
    return snapshot
