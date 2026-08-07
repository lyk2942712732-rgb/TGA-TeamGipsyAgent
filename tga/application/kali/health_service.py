"""Solver-scoped Kali health projections for API and UI diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tga.sandbox.readiness import inspect_kali_runtime_readiness

if TYPE_CHECKING:
    from tga.application.capabilities import CapabilityAssignmentService


class SolverKaliHealthService:
    def __init__(self, assignments: CapabilityAssignmentService | None = None) -> None:
        if assignments is None:
            from tga.application.capabilities import CapabilityAssignmentService

            assignments = CapabilityAssignmentService()
        self.assignments = assignments

    def all(self) -> tuple[dict[str, Any], ...]:
        readiness = inspect_kali_runtime_readiness(self.assignments.kali_profiles.config)
        return tuple(
            self._health(definition, readiness=readiness, summary=True)
            for definition in self.assignments.definitions.all()
        )

    def require(self, solver_id: str) -> dict[str, Any]:
        definition = self.assignments.definitions.require(solver_id)
        readiness = inspect_kali_runtime_readiness(self.assignments.kali_profiles.config)
        return self._health(definition, readiness=readiness, summary=False)

    def _health(self, definition, *, readiness, summary: bool) -> dict[str, Any]:
        binding = definition.kali
        if binding is None:
            payload = {
                "solver_id": definition.id,
                "requires_kali": False,
                "profile_id": None,
                "status": "host_only",
            }
            if summary:
                return payload
            return {
                **payload,
                "image": None,
                "image_status": "not_applicable",
                "runtime_status": "not_applicable",
                "checked_at": None,
                "reasons": [],
                "missing_executables": [],
                "image_store": {"status": "not_applicable", "error": None},
                "toolset": {
                    "expected_digest": None,
                    "actual_digest": None,
                    "status": "not_applicable",
                },
            }

        profile_id = binding.profile_id
        profile = self.assignments.kali_profiles.config.profiles.get(profile_id)
        profile_health = readiness.profiles.get(profile_id)
        if profile is None or profile_health is None:
            payload = {
                "solver_id": definition.id,
                "requires_kali": True,
                "profile_id": profile_id,
                "status": "unknown",
            }
            if summary:
                return payload
            return {
                **payload,
                "image": None,
                "image_status": "configuration_error",
                "runtime_status": readiness.runtime_mode,
                "checked_at": readiness.checked_at,
                "reasons": [{
                    "code": "profile_not_found",
                    "message": f"Kali Profile {profile_id} does not exist.",
                }],
                "missing_executables": [],
                "image_store": {"status": "unknown", "error": None},
                "toolset": {
                    "expected_digest": None,
                    "actual_digest": None,
                    "status": "not_checked",
                },
            }

        payload = {
            "solver_id": definition.id,
            "requires_kali": True,
            "profile_id": profile_id,
            "status": profile_health.status,
        }
        if summary:
            return payload
        return {
            **payload,
            "image": profile_health.image,
            "image_status": profile_health.image_status,
            "runtime_status": profile_health.runtime_status,
            "checked_at": readiness.checked_at,
            "reasons": [
                {"code": _reason_code(profile_health.status), "message": reason}
                for reason in profile_health.reasons
            ],
            "missing_executables": list(profile_health.missing_executables),
            "image_store": {
                "status": profile_health.image_store_status,
                "error": profile_health.image_store_error,
            },
            "toolset": {
                "expected_digest": profile_health.expected_toolset_digest,
                "actual_digest": profile_health.actual_toolset_digest,
                "status": (
                    "mismatch"
                    if profile_health.status == "toolset_mismatch"
                    else "verified_at_acquire"
                    if profile_health.status == "healthy"
                    else "not_checked"
                ),
            },
        }


def _reason_code(status: str) -> str:
    return {
        "unresolved_digest": "unresolved_image_digest",
        "runtime_disabled": "runtime_disabled",
        "runtime_unavailable": "runtime_unavailable",
        "image_unverified": "image_unverified",
        "toolset_mismatch": "toolset_mismatch",
        "tools_missing": "required_executables_missing",
    }.get(status, "readiness_unknown")


__all__ = ["SolverKaliHealthService"]
