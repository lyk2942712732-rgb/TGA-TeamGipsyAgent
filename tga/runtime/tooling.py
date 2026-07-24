"""Tool definition and dispatch boundaries for the native runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tga.contracts import TGATask
from tga.runtime.completion_validators import finish_tool_schema
from tga.tools.mcp_gateway import TGA_MCP_TOOL, gateway_definition


FINISH_TOOL = "finish_session"
INPUT_TOOLS = {"input_list", "input_get", "input_read", "input_search", "input_view", "input_materialize"}


@dataclass(frozen=True)
class ToolExecutionResponse:
    ok: bool
    status: str
    summary: str = ""
    error: dict[str, Any] | None = None
    artifact_ids: list[str] | None = None
    candidate_flags: list[str] | None = None
    payload: dict[str, Any] | None = None

    def model_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "summary": self.summary,
            "error": self.error,
            "artifact_ids": self.artifact_ids or [],
            "candidate_flags": self.candidate_flags or [],
            **(self.payload or {}),
        }


class ToolDefinitionBuilder:
    def __init__(self, *, task: TGATask, registry, tool_names: dict[str, str], mcp_snapshot):
        self.task = task
        self.registry = registry
        self.tool_names = tool_names
        self.mcp_snapshot = mcp_snapshot

    def build(self) -> list[dict[str, Any]]:
        direct_names = {item.provider_name for item in self.task.mcp_capabilities.tools}
        collisions = direct_names.intersection({*self.tool_names, FINISH_TOOL, TGA_MCP_TOOL, *INPUT_TOOLS})
        has_mcp = bool(self.task.mcp_capabilities.server_ids)
        if collisions or (has_mcp and TGA_MCP_TOOL in self.tool_names):
            raise ValueError(f"MCP tool name collision: {', '.join(sorted(collisions or {TGA_MCP_TOOL}))}")

        snapshot = {item["name"]: item for item in self.registry.snapshot()["capabilities"]}
        tools: list[dict[str, Any]] = []
        for provider_name, capability in self.tool_names.items():
            item = snapshot[capability]
            parameters = json.loads(json.dumps(item["input_schema"]))
            parameters.setdefault("properties", {})["_tga"] = self.governance_schema()
            tools.append({"type": "function", "function": {
                "name": provider_name,
                "description": item.get("description") or f"Execute {capability}",
                "parameters": parameters,
            }})
        if has_mcp:
            tools.append(gateway_definition())
        for item in self.mcp_snapshot.function_tools():
            function = item["function"]
            if function.get("name") not in direct_names:
                continue
            parameters = json.loads(json.dumps(function.get("parameters") or {}))
            parameters.setdefault("type", "object")
            properties = parameters.setdefault("properties", {})
            if isinstance(properties, dict):
                properties["_tga"] = self.governance_schema()
            tools.append({"type": "function", "function": {**function, "parameters": parameters}})
        tools.extend(self.input_definitions())
        tools.append({"type": "function", "function": {
            "name": FINISH_TOOL,
            "description": "Submit only after the complete goal passes the evidence gate. A rejection returns missing conditions and execution continues.",
            "parameters": finish_tool_schema(self.task.mode),
        }})
        return tools

    @classmethod
    def input_definitions(cls) -> list[dict[str, Any]]:
        definitions = {
            "input_list": ("List the stable Input Manifest without loading file contents.", {"type": "object", "additionalProperties": False, "properties": {}}),
            "input_get": ("Get metadata and a safe summary for one input.", {"type": "object", "additionalProperties": False, "required": ["input_id"], "properties": {"input_id": {"type": "string"}}}),
            "input_read": ("Read a bounded text segment from a task input or authorized MCP Resource.", {"type": "object", "additionalProperties": False, "required": ["input_id"], "properties": {"input_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 262144}}}),
            "input_search": ("Search a bounded textual input without injecting its complete content.", {"type": "object", "additionalProperties": False, "required": ["input_id", "query"], "properties": {"input_id": {"type": "string"}, "query": {"type": "string", "maxLength": 256}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}),
            "input_view": ("Load an image as a real model image content block.", {"type": "object", "additionalProperties": False, "required": ["input_id"], "properties": {"input_id": {"type": "string"}}}),
            "input_materialize": ("Copy an immutable input into the Session Workspace and return its /workspace path and Artifact.", {"type": "object", "additionalProperties": False, "required": ["input_id"], "properties": {"input_id": {"type": "string"}, "extract_archive": {"type": "boolean"}}}),
        }
        tools = []
        for name, (description, parameters) in definitions.items():
            parameters["properties"]["_tga"] = cls.governance_schema()
            tools.append({"type": "function", "function": {"name": name, "description": description, "parameters": parameters}})
        return tools

    @staticmethod
    def governance_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "description": "Strategy linkage and expected evidence for this action.",
            "properties": {
                "strategy_card_id": {"type": "string"},
                "strategy_step_id": {"type": "string"},
                "rationale": {"type": "string", "maxLength": 500},
                "expected_outcome": {"type": "string", "maxLength": 500},
                "retry_reason": {"type": "string", "maxLength": 500},
                "alternative_analysis": {"type": "string", "maxLength": 500},
                "effect": {
                    "type": "object",
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


class ToolDispatcher:
    def __init__(self, *, capability_handler, input_handler, mcp_handler, completion_handler, direct_mcp_names=None):
        self.capability_handler = capability_handler
        self.input_handler = input_handler
        self.mcp_handler = mcp_handler
        self.completion_handler = completion_handler
        self.direct_mcp_names = direct_mcp_names or (lambda: set())

    def dispatch(self, *, task: TGATask, call: dict[str, Any]) -> dict[str, Any]:
        function = call.get("function") or {}
        tool_name = str(function.get("name") or "")
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            return {"ok": False, "status": "blocked", "error": {"code": "INVALID_TOOL_ARGUMENTS", "message": str(exc)[:500]}}
        if not isinstance(arguments, dict):
            return {"ok": False, "status": "blocked", "error": {"code": "INVALID_TOOL_ARGUMENTS", "message": "tool arguments must be an object"}}
        governance = arguments.pop("_tga", {})
        if not isinstance(governance, dict):
            return {"ok": False, "status": "blocked", "error": {"code": "INVALID_GOVERNANCE_METADATA", "message": "_tga must be an object"}}
        if tool_name == "finish_session":
            return self.completion_handler(arguments=arguments)
        if tool_name.startswith("input_"):
            return self.input_handler(call=call, name=tool_name, arguments=arguments, governance=governance)
        if tool_name == "tga_mcp":
            return self.mcp_handler(call=call, arguments=arguments, governance=governance, direct=False)
        if tool_name in self.direct_mcp_names():
            return self.mcp_handler(call=call, arguments=arguments, governance=governance, direct=True)
        return self.capability_handler(call=call, tool_name=tool_name, arguments=arguments, governance=governance)
