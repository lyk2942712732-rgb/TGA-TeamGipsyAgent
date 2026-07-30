"""Intersection-based Solver tool-manifest construction."""

from __future__ import annotations

from tga.runtime.tooling.catalog.definitions import RuntimeToolCatalog, ToolCatalogEntry
from tga.runtime.tooling.catalog.manifest import SolverToolManifest


ROLE_CONTROL_CAPABILITIES = {
    "supervisor": {
        "inspect_task_state", "create_intent", "update_global_plan", "spawn_solver",
        "inspect_worker_result", "request_review", "request_report",
        "propose_task_completion", "confirm_finding",
    },
    "worker": {"update_local_plan", "propose_knowledge", "submit_worker_result"},
    "reviewer": {"review_evidence", "review_finding", "request_more_evidence"},
    "reporter": {"report.write"},
}

ROLE_RESOURCE_CAPABILITIES = {
    "supervisor": {"input_list", "input_get", "input_read", "input_search", "input_view", "artifact.inspect"},
    "worker": {"input_list", "input_get", "input_read", "input_search", "input_view", "workspace.read", "artifact.inspect"},
    "reviewer": {"artifact.inspect", "evidence.inspect", "knowledge.inspect"},
    "reporter": {"confirmed_knowledge.read", "confirmed_evidence.read", "confirmed_findings.read"},
}


class ToolManifestBuilder:
    def build(self, *, task, solver, definition, intent, catalog: RuntimeToolCatalog) -> SolverToolManifest:
        role = solver.orchestration_role
        allowed_groups = set(definition.allowed_tool_groups).intersection(
            solver.tool_policy_snapshot.allowed_tool_groups
        )
        allowed_capabilities = set(solver.tool_policy_snapshot.allowed_capabilities)
        compatibility_supervisor = (
            role == "supervisor"
            and solver.tool_policy_snapshot.profile == "phase5-single-solver-compatibility"
        )
        values: list[ToolCatalogEntry] = []
        for entry in catalog.entries:
            if entry.tool_class not in allowed_groups:
                continue
            if entry.tool_class == "control":
                if entry.capability not in ROLE_CONTROL_CAPABILITIES[role]:
                    continue
                if entry.capability == "propose_task_completion" and solver.completion_authority != "task":
                    continue
            elif entry.tool_class == "resource_read":
                if entry.capability not in ROLE_RESOURCE_CAPABILITIES[role]:
                    continue
                if entry.capability in {
                    "workspace.read", "artifact.inspect",
                } and entry.capability not in allowed_capabilities:
                    continue
            elif entry.tool_class == "execution":
                if role != "worker" and not compatibility_supervisor:
                    continue
                if entry.capability not in allowed_capabilities and not entry.capability.startswith("mcp:"):
                    continue
                if entry.capability == "mcp.gateway" and not any(
                    item.startswith("mcp:") for item in allowed_capabilities
                ):
                    continue
                if entry.capability == "http.request" and task.execution_policy.network.access == "disabled":
                    continue
                if entry.capability in {"workspace.python", "workspace.shell"} and task.execution_policy.local_compute.mode == "disabled":
                    continue
                specialty_match = not entry.specialties or bool(
                    set(entry.specialties).intersection(solver.specialties)
                )
                required = entry.capability in definition.required_capabilities
                if not specialty_match and not required and not compatibility_supervisor:
                    continue
            elif entry.tool_class == "retrieval":
                if entry.capability != "retrieval.search":
                    continue
            values.append(entry)
        return SolverToolManifest(
            task_id=task.id,
            solver_id=solver.id,
            solver_definition_id=definition.id,
            intent_id=getattr(intent, "id", None),
            entries=tuple(values),
            policy_fingerprints=(
                solver.tool_policy_snapshot.content_sha256,
                solver.definition_content_sha256
                if hasattr(solver, "definition_content_sha256")
                else definition.content_sha256,
            ),
        )


__all__ = ["ToolManifestBuilder"]
