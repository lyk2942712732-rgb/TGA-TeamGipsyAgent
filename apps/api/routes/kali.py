"""Kali Profile management projections."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response, status

from tga.application.capabilities import CapabilityAssignmentService
from tga.application.kali import (
    KaliProfileCreateCommand,
    KaliProfileUpdateCommand,
)
from tga.infrastructure.persistence.errors import PersistenceConflict


router = APIRouter(prefix="/kali/profiles", tags=["kali"])


def _assignments() -> CapabilityAssignmentService:
    return CapabilityAssignmentService()


def _profile_payload(
    assignments: CapabilityAssignmentService, profile_id: str
) -> dict[str, Any]:
    try:
        solver_ids = assignments.solvers_for_kali_profile(profile_id)
        detail = assignments.kali_profiles.detail(
            profile_id, assigned_solver_ids=solver_ids
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Kali Profile not found") from exc
    return detail.model_dump(mode="json")


def _require_current_revision(
    assignments: CapabilityAssignmentService,
    payload: KaliProfileCreateCommand | KaliProfileUpdateCommand,
) -> None:
    if payload.config_sha256 != assignments.kali_profiles.config_sha256:
        raise HTTPException(
            status_code=409,
            detail="Kali Profile configuration changed; reload before saving",
        )


@router.get("")
def kali_profiles() -> dict[str, Any]:
    assignments = _assignments()
    items = [
        _profile_payload(assignments, profile.id)
        for profile in assignments.kali_profiles.all()
    ]
    return {"items": items, "total": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_kali_profile(payload: KaliProfileCreateCommand) -> dict[str, Any]:
    assignments = _assignments()
    _require_current_revision(assignments, payload)
    try:
        assignments.kali_profiles.create(payload)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PersistenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _profile_payload(_assignments(), payload.id)


@router.get("/{profile_id}")
def kali_profile(profile_id: str) -> dict[str, Any]:
    return _profile_payload(_assignments(), profile_id)


@router.put("/{profile_id}")
def update_kali_profile(
    profile_id: str, payload: KaliProfileUpdateCommand
) -> dict[str, Any]:
    assignments = _assignments()
    _require_current_revision(assignments, payload)
    try:
        assignments.kali_profiles.update(profile_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Kali Profile not found") from exc
    except PersistenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _profile_payload(_assignments(), profile_id)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kali_profile(profile_id: str) -> Response:
    assignments = _assignments()
    try:
        solver_ids = assignments.solvers_for_kali_profile(profile_id)
        if solver_ids:
            raise HTTPException(
                status_code=409,
                detail=f"Kali Profile is assigned to Solvers: {list(solver_ids)}",
            )
        assignments.kali_profiles.delete(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Kali Profile not found") from exc
    except PersistenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{profile_id}/solvers")
def kali_profile_solvers(profile_id: str) -> dict[str, Any]:
    assignments = _assignments()
    try:
        solver_ids = assignments.solvers_for_kali_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Kali Profile not found") from exc
    return {"profile_id": profile_id, "solver_ids": list(solver_ids), "total": len(solver_ids)}


@router.post("/{profile_id}/verify")
def verify_kali_profile(profile_id: str) -> dict[str, Any]:
    assignments = _assignments()
    profile = _profile_payload(assignments, profile_id)
    return {
        "ok": False,
        "status": "not_implemented",
        "profile_id": profile_id,
        "supported_capabilities": profile["supported_capabilities"],
        "tool_count": len(profile["tools"]),
        "image_digest": profile["image_digest"],
    }


@router.post("/{profile_id}/refresh-tool-inventory")
def refresh_kali_tool_inventory(profile_id: str) -> dict[str, Any]:
    assignments = _assignments()
    try:
        profile = assignments.kali_profiles.refresh_tool_inventory(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Kali Profile not found") from exc
    return {
        "ok": False,
        "status": "not_implemented",
        "profile_id": profile_id,
        "tool_count": len(profile.tools),
    }
