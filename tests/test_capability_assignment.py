from __future__ import annotations

import pytest
from pydantic import ValidationError

from tga.application.capabilities import CapabilityAssignmentService
from tga.application.capabilities import HostCapabilityRegistry
from tga.application.kali import KaliProfileService
from tga.domain.capabilities import SolverKaliBinding
from tga.domain.governance.models import ExecutionPolicy
from tga.domain.governance.models import HighImpactExecutionPolicy
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


def test_manifest_uses_frozen_capability_and_kali_runtime_snapshot() -> None:
    assignments = CapabilityAssignmentService()
    definition = assignments.definitions.require("ctf-pwn-solver")
    kali_runtime = assignments.resolve_kali(definition)
    assert kali_runtime is not None
    snapshot = type("Snapshot", (), {
        "host_capability_ids": tuple(
            item.id for item in assignments.resolve_host(definition)
        ),
        "host_capability_profile_id": definition.host_capability_profile_id,
        "kali": definition.kali,
        "kali_runtime": kali_runtime,
    })()
    changed = definition.model_copy(update={
        "host_capability_overrides": definition.host_capability_overrides.model_copy(
            update={"remove": (snapshot.host_capability_ids[0],)}
        ),
        "kali": None,
    })

    manifest = assignments.manifest(
        task_id="task-test",
        solver_id="solver-test",
        definition=changed,
        intent_id=None,
        capability_snapshot=snapshot,
    )

    assert tuple(item.id for item in manifest.host_capabilities) == snapshot.host_capability_ids
    assert manifest.kali == kali_runtime


def test_manifest_hides_kali_when_local_compute_is_disabled() -> None:
    assignments = CapabilityAssignmentService()
    definition = assignments.definitions.require("ctf-pwn-solver")

    manifest = assignments.manifest(
        task_id="task-test",
        solver_id="solver-test",
        definition=definition,
        intent_id=None,
        execution_policy=ExecutionPolicy(),
    )

    assert manifest.kali is None


def test_manifest_filters_high_impact_host_capabilities_from_task_policy() -> None:
    assignments = CapabilityAssignmentService()
    definition = assignments.definitions.require("ctf-web-solver")
    forbidden = ExecutionPolicy()

    hidden = assignments.manifest(
        task_id="task-test", solver_id="solver-test", definition=definition,
        intent_id=None, execution_policy=forbidden,
    )
    approval = assignments.manifest(
        task_id="task-test", solver_id="solver-test", definition=definition,
        intent_id=None,
        execution_policy=forbidden.model_copy(update={
            "high_impact": HighImpactExecutionPolicy(mode="approval_required")
        }),
    )
    allowlisted = assignments.manifest(
        task_id="task-test", solver_id="solver-test", definition=definition,
        intent_id=None,
        execution_policy=forbidden.model_copy(update={
            "high_impact": HighImpactExecutionPolicy(
                mode="allowlisted", allowed_actions=["artifact.publish"]
            )
        }),
    )

    assert "artifact.publish" not in {item.id for item in hidden.host_capabilities}
    assert "input.materialize" not in {item.id for item in hidden.host_capabilities}
    assert "artifact.publish" in {item.id for item in approval.host_capabilities}
    assert "input.materialize" in {item.id for item in approval.host_capabilities}
    assert "artifact.publish" in {item.id for item in allowlisted.host_capabilities}
    assert "input.materialize" not in {item.id for item in allowlisted.host_capabilities}


def test_manifest_uses_complete_frozen_host_entries_after_registry_changes() -> None:
    assignments = CapabilityAssignmentService()
    definition = assignments.definitions.require("ctf-supervisor")
    frozen = assignments.resolve_host(definition)
    snapshot = type("Snapshot", (), {
        "host_capability_ids": tuple(item.id for item in frozen),
        "host_capabilities": frozen,
        "host_capability_profile_id": definition.host_capability_profile_id,
        "kali": definition.kali,
        "kali_runtime": None,
    })()
    changed_definitions = tuple(
        item.model_copy(update={
            "description": f"changed current description for {item.id}",
            "input_schema": {"type": "object", "properties": {"changed": {"type": "boolean"}}},
        })
        for item in assignments.host_registry.all()
    )
    changed_assignments = CapabilityAssignmentService(
        host_registry=HostCapabilityRegistry(
            changed_definitions, assignments.host_registry.profiles()
        ),
    )

    manifest = changed_assignments.manifest(
        task_id="task-test",
        solver_id="solver-test",
        definition=definition,
        intent_id=None,
        capability_snapshot=snapshot,
    )

    assert manifest.host_capabilities == frozen
