"""Runtime tooling package with a phase-1 compatibility bridge."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_MODULE_NAME = "tga.runtime._legacy_tooling"
_LEGACY_PATH = Path(__file__).resolve().parent.parent / "tooling.py"
_spec = importlib.util.spec_from_file_location(_MODULE_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - invalid installation
    raise ImportError(f"cannot load legacy runtime tooling from {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE_NAME] = _legacy
_spec.loader.exec_module(_legacy)

ToolExecutionResponse = _legacy.ToolExecutionResponse
class ToolDefinitionBuilder:
    """Generate schemas from a Solver manifest, with a non-v6 probe fallback."""

    def __init__(self, *, manifest=None, **legacy_kwargs):
        self.manifest = manifest
        self.legacy_kwargs = legacy_kwargs

    def build(self):
        if self.manifest is None:
            return _legacy.ToolDefinitionBuilder(**self.legacy_kwargs).build()
        import json

        tools = []
        for entry in self.manifest.entries:
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

ToolDispatcher = _legacy.ToolDispatcher

__all__ = ["ToolDefinitionBuilder", "ToolDispatcher", "ToolExecutionResponse"]
