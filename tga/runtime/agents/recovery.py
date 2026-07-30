"""Recovery of persisted approval decisions without duplicate execution."""

from __future__ import annotations

import json
from typing import Any


class ApprovalRecovery:
    def __init__(self, *, store, handlers, messages: list[dict[str, Any]], save, gateway=None) -> None:
        self.store = store
        self.handlers = handlers
        self.messages = messages
        self.save = save
        self.gateway = gateway

    def consume_one(self, task_id: str) -> bool:
        existing_call_ids = {
            str(item.get("tool_call_id") or "")
            for item in self.messages
            if item.get("role") == "tool"
        }
        for item in self.store.list_actions(task_id):
            if item.get("status") not in {"approved", "rejected", "expired"}:
                continue
            action = self.store.get_action_spec(task_id, str(item["id"]))
            if action is None:
                continue
            call_id = str(action.authorization.get("tool_call_id") or "")
            if not call_id or call_id in existing_call_ids:
                continue
            if item.get("status") == "approved":
                result = (
                    self.gateway.resume_approved(action).model_payload
                    if self.gateway is not None and action.governed_action_id
                    else (
                        self.handlers.mcp.execute_approved(action)
                        if action.authorization.get("mcp_server")
                        else self.handlers.capability.execute_approved(action)
                    )
                )
            else:
                persisted_result = item.get("result") if isinstance(item.get("result"), dict) else {}
                persisted_error = (
                    persisted_result.get("error")
                    if isinstance(persisted_result.get("error"), dict) else {}
                )
                result = {
                    "ok": False,
                    "status": str(item.get("status")),
                    "action_id": action.id,
                    "summary": str(
                        persisted_result.get("summary")
                        or "The high-impact action was rejected."
                    ),
                    "error": {
                        "code": str(
                            persisted_error.get("code") or (
                                "ACTION_APPROVAL_EXPIRED" if item.get("status") == "expired"
                                else "ACTION_REJECTED_BY_USER"
                            )
                        ),
                        "message": str(
                            persisted_error.get("message")
                            or "The user rejected this high-impact action."
                        ),
                        "retryable": bool(persisted_error.get("retryable")),
                    },
                }
                if self.gateway is not None and action.governed_action_id:
                    result = self.gateway.resolve_without_execution(
                        action, status=str(item.get("status")), payload=result
                    ).model_payload
            self.messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": str(
                    action.authorization.get("provider_tool_name") or action.capability
                ),
                "content": json.dumps(result, ensure_ascii=False),
            })
            self.save()
            return True
        return False


__all__ = ["ApprovalRecovery"]
