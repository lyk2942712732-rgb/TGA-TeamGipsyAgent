"""Explicit, secret-free Provider capability verification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from tga.contracts import TGATask
from tga.models.base import ModelMessage
from tga.runtime.tooling import ToolDefinitionBuilder
from tga.tools.mcp_registry import MCPCatalogSnapshot


_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "verify_tga_action_protocol",
        "description": "Return a harmless protocol verification result.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    },
}


@dataclass(frozen=True)
class ProviderCapabilityProbe:
    """Run the same tool protocol checks used by the product runtime.

    Responses are validated in-memory and never returned or persisted.  The
    caller receives only counts and capability booleans safe for settings UI.
    """

    client: Any

    def verify(self) -> dict[str, Any]:
        basic = self._basic_connection()
        forced = self._forced_tool_call()
        automatic = self._automatic_tool_call()
        product_tools = self._product_tools()
        catalog = self._catalog_acceptance(product_tools)
        return {
            "request_id": basic.get("request_id") or forced.get("request_id") or automatic.get("request_id"),
            "capabilities": {
                "chat_completions": True,
                "tool_calling": True,
                "forced_tool_choice": True,
                "auto_tool_choice": True,
                "vision": getattr(self.client, "supports_vision", None),
                "reasoning_content": getattr(self.client, "reasoning_mode", "auto") != "disabled",
            },
            "tool_catalog": {
                "tool_count": len(product_tools),
                "schema_bytes": len(json.dumps(product_tools, ensure_ascii=True, separators=(",", ":")).encode("utf-8")),
                "accepted": bool(catalog.get("accepted")),
            },
        }

    def _basic_connection(self) -> dict[str, Any]:
        response = self.client.chat(
            [ModelMessage(role="user", content="Reply with a short acknowledgement.")],
            temperature=0,
        )
        if not isinstance(response.content, str):
            raise RuntimeError("provider basic connection did not return an assistant message")
        return {"request_id": (response.raw or {}).get("id") if isinstance(response.raw, dict) else None}

    def _forced_tool_call(self) -> dict[str, Any]:
        response = self.client.chat_action_tool(
            [
                ModelMessage(role="system", content="Use the requested verification tool exactly once."),
                ModelMessage(role="user", content="Verify the tool-calling protocol now."),
            ],
            tool_name="verify_tga_action_protocol",
            tool_description=str(_PROBE_TOOL["function"]["description"]),
            parameters=dict(_PROBE_TOOL["function"]["parameters"]),
            thinking=None,
            temperature=0,
        )
        try:
            arguments = json.loads(response.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("provider forced tool call returned invalid JSON arguments") from exc
        if not isinstance(arguments, dict):
            raise RuntimeError("provider forced tool call arguments must be an object")
        return {"request_id": (response.raw or {}).get("id") if isinstance(response.raw, dict) else None}

    def _automatic_tool_call(self) -> dict[str, Any]:
        result = self.client.preflight_tools([_PROBE_TOOL])
        if not result.get("ok"):
            raise RuntimeError("provider automatic tool call verification failed")
        return result

    def _product_tools(self) -> list[dict[str, Any]]:
        task = TGATask(id="provider_probe", name="Provider probe", mode="ctf", goal="verify protocol")
        from tga.capabilities.registry import build_default_registry

        registry = build_default_registry()
        names = {
            item["name"].replace(".", "_"): item["name"]
            for item in registry.snapshot()["capabilities"]
            if task.mode in item["modes"]
        }
        return ToolDefinitionBuilder(
            task=task,
            solver_definition=SimpleNamespace(sandbox_profile_id=None),
            registry=registry,
            tool_names=names,
            mcp_snapshot=MCPCatalogSnapshot(version="provider_probe"),
        ).build()

    def _catalog_acceptance(self, tools: list[dict[str, Any]]) -> dict[str, Any]:
        result = self.client.chat_tools(
            [{"role": "user", "content": "Acknowledge this tool catalog without executing any tool."}],
            tools=tools, temperature=0, max_tokens=512,
        )
        if not isinstance(result.get("message"), dict):
            raise RuntimeError("provider rejected the product tool catalog")
        return {"accepted": True}
