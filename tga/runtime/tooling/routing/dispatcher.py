"""Provider-call parser that injects host ActionContext before governance."""

from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import ValidationError

from tga.runtime.tooling.requests import ModelToolIntent, ToolRequest


class GatewayToolDispatcher:
    def __init__(self, *, gateway, action_context: Callable[[], Any]) -> None:
        self.gateway = gateway
        self.action_context = action_context

    def dispatch(self, *, task, call: dict[str, Any]) -> dict[str, Any]:
        function = call.get("function") or {}
        provider_name = str(function.get("name") or "")
        raw = function.get("arguments") or "{}"
        try:
            arguments = raw if isinstance(raw, dict) else json.loads(raw)
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be an object")
        except (TypeError, json.JSONDecodeError) as exc:
            return self._error("INVALID_TOOL_ARGUMENTS", str(exc))
        raw_intent = arguments.pop("_tga", {})
        if not isinstance(raw_intent, dict):
            return self._error("INVALID_MODEL_TOOL_INTENT", "_tga must be an object")
        # One release-window compatibility for existing clients; the canonical
        # v6 field and generated schema are `proposed_effect`.
        if "effect" in raw_intent and "proposed_effect" not in raw_intent:
            raw_intent["proposed_effect"] = raw_intent.pop("effect")
        try:
            model_intent = ModelToolIntent.model_validate(raw_intent)
        except ValidationError as exc:
            return self._error("INVALID_MODEL_TOOL_INTENT", str(exc)[:1_000])
        context = self.action_context()
        if provider_name == "finish_session" and context.orchestration_role == "supervisor":
            # Accepted for persisted Phase-5 transcripts, but never emitted in
            # a schema-v6 SolverToolManifest.
            provider_name = "propose_task_completion"
        try:
            request = ToolRequest(
                provider_tool_name=provider_name,
                arguments=arguments,
                model_intent=model_intent,
                action_context=context,
                tool_call_id=str(call.get("id") or "missing_tool_call_id"),
            )
        except ValidationError as exc:
            return self._error("INVALID_TOOL_REQUEST", str(exc)[:1_000])
        return self.gateway.handle(request).model_payload

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "blocked",
            "error": {"code": code, "message": message, "retryable": False},
        }


__all__ = ["GatewayToolDispatcher"]
