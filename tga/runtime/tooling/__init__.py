"""Schema-v6 runtime tool definitions."""

from __future__ import annotations

import json


class ToolDefinitionBuilder:
    """Generate provider schemas from the immutable SolverToolManifest."""

    def __init__(self, *, manifest=None, task=None, solver_definition=None, registry=None, tool_names=None, mcp_snapshot=None):
        self.manifest = manifest
        self.task = task
        self.solver_definition = solver_definition
        self.registry = registry
        self.tool_names = tool_names or {}
        self.mcp_snapshot = mcp_snapshot

    def build(self):
        entries = self.manifest.entries if self.manifest is not None else self._probe_entries()
        tools = []
        for entry in entries:
            parameters = json.loads(json.dumps(entry.parameters))
            parameters.setdefault("type", "object")
            properties = parameters.setdefault("properties", {})
            if isinstance(properties, dict):
                properties["_tga"] = self.governance_schema()
            tools.append({
                "type": "function",
                "function": {
                    "name": entry.provider_tool_name,
                    "description": entry.description,
                    "parameters": parameters,
                },
            })
        return tools

    def _probe_entries(self):
        if self.task is None or self.solver_definition is None or self.registry is None or self.mcp_snapshot is None:
            raise ValueError("manifest is required for runtime tool definitions")
        from tga.runtime.tooling.catalog import RuntimeToolCatalog

        return RuntimeToolCatalog.from_runtime(
            task=self.task,
            solver_definition=self.solver_definition,
            registry=self.registry,
            tool_names=self.tool_names,
            mcp_snapshot=self.mcp_snapshot,
        ).entries

    @staticmethod
    def governance_schema():
        return {
            "type": "object",
            "additionalProperties": False,
            "description": "Non-authoritative rationale and expected outcome. Ownership is host-injected.",
            "properties": {
                "rationale": {"type": "string", "maxLength": 500},
                "expected_outcome": {"type": "string", "maxLength": 500},
                "retry_reason": {"type": ["string", "null"], "maxLength": 500},
                "alternative_analysis": {"type": ["string", "null"], "maxLength": 500},
                "proposed_effect": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "properties": {
                        "scope": {"enum": ["none", "session", "workspace", "target"]},
                        "persistence": {"enum": ["none", "temporary", "persistent"]},
                        "reversibility": {"enum": ["not_applicable", "reversible", "uncertain", "irreversible"]},
                        "category": {"enum": ["authentication", "submission", "file_write", "resource_create", "resource_modify", "resource_delete", "containment", "destructive_scan"]},
                        "description": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                },
            },
        }

__all__ = ["ToolDefinitionBuilder"]
