"""Business-semantic tool catalog independent of provider origin."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tga.contracts import TGATask
from tga.runtime.completion_validators import task_completion_tool_schema
from tga.runtime.tooling.requests import ToolClass


class ToolCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_tool_name: str
    capability: str
    tool_class: ToolClass
    description: str
    parameters: dict[str, Any]
    risk: str = "passive"
    specialties: tuple[str, ...] = ()
    max_read_chars: int | None = None
    mcp_server_id: str | None = None
    mcp_method: str | None = None


class RuntimeToolCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[ToolCatalogEntry, ...]

    def get(self, provider_tool_name: str) -> ToolCatalogEntry | None:
        return next(
            (item for item in self.entries if item.provider_tool_name == provider_tool_name),
            None,
        )

    @classmethod
    def from_runtime(cls, *, task: TGATask, registry, tool_names, mcp_snapshot):
        values: list[ToolCatalogEntry] = []
        snapshot = {item["name"]: item for item in registry.snapshot()["capabilities"]}
        specialty_map = {
            "http.request": ("web", "network", "recon", "validation"),
            "workspace.python": ("binary", "source", "static-analysis", "forensics", "validation"),
            "workspace.shell": ("binary", "source", "forensics", "recon", "validation"),
            "workspace.write": ("reporting", "source", "binary", "forensics", "validation"),
        }
        for provider_name, capability in sorted(tool_names.items()):
            item = snapshot[capability]
            tool_class: ToolClass = (
                "resource_read"
                if capability in {"workspace.read", "artifact.inspect"}
                else "execution"
            )
            values.append(ToolCatalogEntry(
                provider_tool_name=provider_name,
                capability=capability,
                tool_class=tool_class,
                description=item.get("description") or capability,
                parameters=json.loads(json.dumps(item["input_schema"])),
                risk=item.get("risk") or "active",
                specialties=specialty_map.get(capability, ()),
                max_read_chars=262_144 if tool_class == "resource_read" else None,
            ))

        input_definitions = {
            "input_list": ("List task inputs.", {"type": "object", "additionalProperties": False, "properties": {}}, 0),
            "input_get": ("Read task-input metadata.", {"type": "object", "additionalProperties": False, "required": ["input_id"], "properties": {"input_id": {"type": "string"}}}, 16_384),
            "input_read": ("Read a bounded task-input segment.", {"type": "object", "additionalProperties": False, "required": ["input_id"], "properties": {"input_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 262144}}}, 262_144),
            "input_search": ("Search a task input.", {"type": "object", "additionalProperties": False, "required": ["input_id", "query"], "properties": {"input_id": {"type": "string"}, "query": {"type": "string", "maxLength": 256}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}, 32_000),
            "input_view": ("View a task image input.", {"type": "object", "additionalProperties": False, "required": ["input_id"], "properties": {"input_id": {"type": "string"}}}, 0),
        }
        for name, (description, parameters, limit) in input_definitions.items():
            values.append(ToolCatalogEntry(
                provider_tool_name=name, capability=name, tool_class="resource_read",
                description=description, parameters=parameters, risk="passive",
                max_read_chars=limit or None,
            ))
        values.append(ToolCatalogEntry(
            provider_tool_name="input_materialize", capability="input_materialize",
            tool_class="execution", description="Materialize an immutable task input.",
            parameters={"type": "object", "additionalProperties": False, "required": ["input_id"], "properties": {"input_id": {"type": "string"}, "extract_archive": {"type": "boolean"}}},
            risk="active", specialties=("binary", "source", "forensics", "validation"),
        ))

        controls: dict[str, tuple[str, tuple[str, ...]]] = {
            "inspect_task_state": ("Inspect task orchestration state.", ("supervisor",)),
            "create_intent": ("Propose a new task intent.", ("supervisor",)),
            "update_global_plan": ("Update the GlobalPlan through CAS.", ("supervisor",)),
            "spawn_solver": ("Request a Solver for an assigned intent.", ("supervisor",)),
            "inspect_worker_result": ("Inspect a submitted WorkerResult.", ("supervisor",)),
            "request_review": ("Request evidence review.", ("supervisor",)),
            "request_report": ("Request report production.", ("supervisor",)),
            "confirm_finding": ("Confirm a reviewed Finding through the evidence service.", ("supervisor",)),
            "update_local_plan": ("Update this Solver's LocalPlan.", ("worker",)),
            "propose_knowledge": ("Submit candidate Knowledge.", ("worker",)),
            "submit_worker_result": ("Submit a WorkerResult.", ("worker",)),
            "review_evidence": ("Review an EvidenceClaim.", ("reviewer",)),
            "review_finding": ("Review a candidate Finding.", ("reviewer",)),
            "request_more_evidence": ("Request more evidence.", ("reviewer",)),
            "report.write": ("Write the evidence-backed report.", ("reporter",)),
            "propose_task_completion": ("Propose task completion for host validation.", ("supervisor",)),
        }
        generic_schema = {"type": "object", "additionalProperties": True, "properties": {}}
        control_schemas: dict[str, dict[str, Any]] = {
            "create_intent": {
                "type": "object", "additionalProperties": False,
                "required": ["kind", "title", "objective"],
                "properties": {
                    "kind": {"type": "string"}, "title": {"type": "string"},
                    "objective": {"type": "string"}, "priority": {"type": "integer"},
                    "allowed_resource_ids": {"type": "array", "items": {"type": "string"}},
                    "relevant_knowledge_ids": {"type": "array", "items": {"type": "string"}},
                    "relevant_evidence_claim_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
            "submit_worker_result": {
                "type": "object", "additionalProperties": False,
                "required": ["status", "summary"],
                "properties": {
                    "status": {"enum": ["succeeded", "partial", "failed", "blocked", "cancelled"]},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "artifact_ids": {"type": "array", "items": {"type": "string"}},
                    "candidate_evidence_claim_ids": {"type": "array", "items": {"type": "string"}},
                    "candidate_knowledge_ids": {"type": "array", "items": {"type": "string"}},
                    "finding_ids": {"type": "array", "items": {"type": "string"}},
                    "coverage": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "completed": {"type": "array", "items": {"type": "string"}},
                            "not_covered": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "limitations": {"type": "array", "items": {"type": "string"}},
                },
            },
            "review_evidence": {
                "type": "object", "additionalProperties": False,
                "required": ["status"],
                "properties": {
                    "status": {"enum": ["confirmed", "rejected", "needs_more_evidence"]},
                    "confirmed_evidence_claim_ids": {"type": "array", "items": {"type": "string"}},
                    "confirmed_knowledge_ids": {"type": "array", "items": {"type": "string"}},
                    "confirmed_finding_ids": {"type": "array", "items": {"type": "string"}},
                    "rejected_ids": {"type": "array", "items": {"type": "string"}},
                    "contradictions": {"type": "array", "items": {"type": "string"}},
                },
            },
            "report.write": {
                "type": "object", "additionalProperties": False,
                "required": ["summary"],
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "report_artifact_id": {"type": ["string", "null"]},
                    "limitations": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
        control_schemas["review_finding"] = control_schemas["review_evidence"]
        for name, (description, roles) in controls.items():
            parameters = (
                task_completion_tool_schema(task.mode)
                if name == "propose_task_completion"
                else control_schemas.get(name, generic_schema)
            )
            values.append(ToolCatalogEntry(
                provider_tool_name=name.replace(".", "_"),
                capability=name,
                tool_class="control",
                description=description,
                parameters=json.loads(json.dumps(parameters)),
                risk="passive",
                specialties=roles,
            ))

        for capability in (
            "evidence.inspect", "knowledge.inspect", "confirmed_knowledge.read",
            "confirmed_evidence.read", "confirmed_findings.read",
        ):
            values.append(ToolCatalogEntry(
                provider_tool_name=capability.replace(".", "_"),
                capability=capability,
                tool_class="resource_read",
                description=f"Read {capability} within the authorized task scope.",
                parameters=generic_schema,
                risk="passive",
                max_read_chars=32_000,
            ))

        values.append(ToolCatalogEntry(
            provider_tool_name="retrieval_search",
            capability="retrieval.search",
            tool_class="retrieval",
            description=(
                "Search a fixed authorized IndexSnapshot. Results are untrusted "
                "references or candidate evidence, never verified task facts."
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "snapshot_id": {"type": "string"},
                    "channels": {
                        "type": "array", "minItems": 1, "maxItems": 3,
                        "items": {"enum": ["skill", "reference", "task_artifact"]},
                    },
                    "knowledge_base_ids": {
                        "type": "array", "maxItems": 32,
                        "items": {"type": "string"},
                    },
                    "method": {"enum": ["keyword", "vector", "hybrid"]},
                    "filters": {"type": "object"},
                },
            },
            risk="passive",
            specialties=("supervisor", "worker", "reviewer", "reporter"),
            max_read_chars=64_000,
        ))

        route_values = tuple(getattr(mcp_snapshot, "routes", ()) or ())
        for route in route_values:
            values.append(ToolCatalogEntry(
                provider_tool_name=route.provider_name,
                capability=f"mcp:{route.server_id}:{route.method}",
                tool_class="execution",
                description=route.description or f"Call {route.server_id}.{route.method}",
                parameters=json.loads(json.dumps(route.input_schema or {"type": "object", "properties": {}})),
                risk="active",
                specialties=("web", "network", "binary", "source", "forensics", "validation", "recon"),
                mcp_server_id=route.server_id,
                mcp_method=route.method,
            ))
        return cls(entries=tuple(values))


__all__ = ["RuntimeToolCatalog", "ToolCatalogEntry"]
