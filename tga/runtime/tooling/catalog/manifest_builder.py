"""Build the capability surface from one authoritative assignment service."""

from __future__ import annotations

from tga.application.capabilities import CapabilityAssignmentService


class ToolManifestBuilder:
    def __init__(self, assignments: CapabilityAssignmentService | None = None) -> None:
        self.assignments = assignments

    def build(
        self, *, task, solver, definition, intent, catalog,
        supported_intent_kinds: tuple[str, ...] = (),
    ):
        assignments = self.assignments or CapabilityAssignmentService()
        fingerprint = solver.capability_binding_snapshot.content_sha256
        execution_policy = getattr(solver, "execution_policy_snapshot", None)
        execution_policy = execution_policy or task.execution_policy
        manifest = assignments.manifest(
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
        if not supported_intent_kinds:
            return manifest
        constrained = []
        for capability in manifest.host_capabilities:
            if capability.id not in {"create_intent", "update_global_plan"}:
                constrained.append(capability)
                continue
            schema = dict(capability.input_schema)
            properties = dict(schema.get("properties") or {})
            properties["kind"] = {
                **dict(properties.get("kind") or {}),
                "enum": list(supported_intent_kinds),
                "description": "Canonical Intent kind accepted by an available Worker.",
            }
            schema["properties"] = properties
            constrained.append(capability.model_copy(update={"input_schema": schema}))
        return manifest.model_copy(update={"host_capabilities": tuple(constrained)})


__all__ = ["ToolManifestBuilder"]
