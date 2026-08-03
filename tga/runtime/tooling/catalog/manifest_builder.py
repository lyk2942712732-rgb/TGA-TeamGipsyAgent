"""Build the capability surface from one authoritative assignment service."""

from __future__ import annotations

from tga.application.capabilities import CapabilityAssignmentService


class ToolManifestBuilder:
    def __init__(self, assignments: CapabilityAssignmentService | None = None) -> None:
        self.assignments = assignments

    def build(self, *, task, solver, definition, intent, catalog):
        assignments = self.assignments or CapabilityAssignmentService()
        fingerprint = solver.capability_binding_snapshot.content_sha256
        execution_policy = getattr(solver, "execution_policy_snapshot", None)
        execution_policy = execution_policy or task.execution_policy
        return assignments.manifest(
            task_id=task.id,
            solver_id=solver.id,
            definition=definition,
            intent_id=getattr(intent, "id", None),
            policy_fingerprints=(
                fingerprint,
                definition.content_sha256,
                __import__("hashlib").sha256(
                    execution_policy.model_dump_json().encode()
                ).hexdigest(),
            ),
            mcp_entries=tuple(catalog.entries),
            execution_policy=execution_policy,
            capability_snapshot=solver.capability_binding_snapshot,
        )


__all__ = ["ToolManifestBuilder"]
