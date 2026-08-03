"""SolverDefinition capability management API."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from tga.application.capabilities import CapabilityAssignmentService
from tga.domain.capabilities import HostCapabilityOverrides, SolverKaliBinding
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry


router = APIRouter(prefix="/solvers", tags=["solvers"])
DEFINITION_ROOT = Path(__file__).resolve().parents[3] / "resources" / "solver_definitions"


class SolverCapabilitiesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_capability_profile_id: str
    host_capability_overrides: HostCapabilityOverrides
    kali: SolverKaliBinding | None = None


def _assignments() -> CapabilityAssignmentService:
    return CapabilityAssignmentService()


@router.get("")
def solvers(query: str = Query(default="", max_length=255)) -> dict[str, Any]:
    assignments = _assignments()
    needle = query.strip().casefold()
    definitions = [
        definition
        for definition in assignments.definitions.all()
        if not needle
        or needle in definition.id.casefold()
        or any(needle in value.casefold() for value in definition.specialties)
    ]
    items = [assignments.definition_detail(item) for item in definitions]
    return {"items": items, "total": len(items)}


@router.get("/{solver_id}")
def solver(solver_id: str) -> dict[str, Any]:
    assignments = _assignments()
    try:
        definition = assignments.definitions.require(solver_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SolverDefinition not found") from exc
    return assignments.definition_detail(definition)


@router.put("/{solver_id}/capabilities")
def update_solver_capabilities(
    solver_id: str, payload: SolverCapabilitiesUpdate
) -> dict[str, Any]:
    assignments = _assignments()
    try:
        definition = assignments.definitions.require(solver_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SolverDefinition not found") from exc
    candidate_payload = definition.model_dump(mode="json", exclude={"content_sha256"})
    candidate_payload.update({
        "host_capability_profile_id": payload.host_capability_profile_id,
        "host_capability_overrides": payload.host_capability_overrides.model_dump(mode="json"),
        "kali": payload.kali.model_dump(mode="json") if payload.kali else None,
    })
    try:
        candidate = type(definition).model_validate({
            **candidate_payload,
            "content_sha256": definition.content_sha256,
        })
        assignments.resolve_host(candidate)
        assignments.resolve_kali(candidate)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    path = _definition_path(solver_id)
    validation_root = DEFINITION_ROOT.parent / f".{DEFINITION_ROOT.name}.{uuid4().hex}.validate"
    try:
        shutil.copytree(DEFINITION_ROOT, validation_root)
        validation_path = validation_root / path.relative_to(DEFINITION_ROOT)
        validation_path.write_text(
            json.dumps(candidate_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        SolverDefinitionRegistry(
            validation_root,
            host_registry=assignments.host_registry,
            kali_profiles=assignments.kali_profiles,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"SolverDefinition update failed: {exc}") from exc
    finally:
        shutil.rmtree(validation_root, ignore_errors=True)

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    original = path.read_bytes()
    try:
        temporary.write_text(
            json.dumps(candidate_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        registry = SolverDefinitionRegistry.builtin()
    except (OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        rollback = path.with_name(f".{path.name}.{uuid4().hex}.rollback")
        try:
            rollback.write_bytes(original)
            os.replace(rollback, path)
        finally:
            rollback.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"SolverDefinition commit failed: {exc}") from exc
    refreshed = CapabilityAssignmentService(definitions=registry)
    return refreshed.definition_detail(registry.require(solver_id))


@router.get("/{solver_id}/manifest-preview")
def solver_manifest_preview(
    solver_id: str,
    mode: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    assignments = _assignments()
    try:
        definition = assignments.definitions.require(solver_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SolverDefinition not found") from exc
    if mode is not None and mode not in definition.supported_modes:
        raise HTTPException(status_code=422, detail="Solver does not support the requested mode")
    manifest = assignments.manifest(
        task_id="manifest-preview",
        solver_id=solver_id,
        definition=definition,
        intent_id=None,
    )
    return manifest.model_dump(mode="json")


def _definition_path(solver_id: str) -> Path:
    matches = []
    for path in DEFINITION_ROOT.rglob("*.json"):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("id") == solver_id:
                matches.append(path)
        except (OSError, json.JSONDecodeError):
            continue
    if len(matches) != 1:
        raise HTTPException(status_code=500, detail="SolverDefinition resource is ambiguous")
    return matches[0]
