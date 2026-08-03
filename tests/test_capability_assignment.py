from __future__ import annotations

import pytest
from pydantic import ValidationError

from tga.application.capabilities import CapabilityAssignmentService
from tga.application.kali import KaliProfileService
from tga.domain.capabilities import SolverKaliBinding
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.runtime.host_handler_contract import (
    RUNTIME_HOST_HANDLER_KEYS,
    validate_runtime_host_handlers,
)


def test_kali_binding_requires_a_non_empty_unique_capability_set() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        SolverKaliBinding(profile_id="ctf-pwn-v1", capabilities=())

    with pytest.raises(ValidationError, match="must be unique"):
        SolverKaliBinding(
            profile_id="ctf-pwn-v1",
            capabilities=("kali.exec", "kali.exec"),
        )


def test_kali_binding_strictly_rejects_boolean_permissions() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SolverKaliBinding.model_validate({
            "profile_id": "ctf-pwn-v1",
            "capabilities": ["kali.exec"],
            "allow_exec": True,
        })


def test_assignment_exposes_exactly_the_bound_kali_capabilities() -> None:
    assignments = CapabilityAssignmentService()
    reviewer = assignments.definitions.require("evidence-reviewer")
    exec_only = assignments.definitions.require("challenge-classifier")
    interactive = assignments.definitions.require("ctf-pwn-solver")

    assert assignments.resolve_kali(reviewer) is None
    assert assignments.resolve_kali(exec_only).capabilities == ("kali.exec",)
    assert assignments.resolve_kali(interactive).capabilities == (
        "kali.exec", "kali.session",
    )

    manifest = assignments.manifest(
        task_id="task-test",
        solver_id="solver-test",
        definition=interactive,
        intent_id=None,
    ).model_dump(mode="json")
    assert manifest["kali"]["capabilities"] == ["kali.exec", "kali.session"]
    assert "allow_exec" not in manifest["kali"]
    assert "allow_session" not in manifest["kali"]


def test_registry_rejects_capability_not_supported_by_profile() -> None:
    profiles = KaliProfileService()
    original = profiles.config.profile("ctf-pwn-v1")
    restricted = original.model_copy(update={
        "supported_capabilities": ("kali.exec",),
        "session_executables": (),
    })
    config = profiles.config.model_copy(update={
        "profiles": {**profiles.config.profiles, "ctf-pwn-v1": restricted},
    })

    with pytest.raises(ValueError, match="does not support capabilities.*kali.session"):
        SolverDefinitionRegistry.builtin(
            kali_profiles=KaliProfileService(config),
        )


def test_host_registry_definitions_have_runtime_handler_routes() -> None:
    assignments = CapabilityAssignmentService()
    capability_ids = {item.id for item in assignments.host_registry.all()}
    assigned_ids = {
        item.id
        for definition in assignments.definitions.all()
        for item in assignments.resolve_host(definition)
    }
    assert assigned_ids == capability_ids
    validate_runtime_host_handlers(assignments.host_registry)


def test_missing_host_handler_fails_startup_validation() -> None:
    registry = CapabilityAssignmentService().host_registry
    missing = RUNTIME_HOST_HANDLER_KEYS - {"task_input.read"}

    with pytest.raises(RuntimeError, match="input.read"):
        registry.validate_handlers(missing)
