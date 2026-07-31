"""Recovery of persisted approval decisions without duplicate execution."""

from __future__ import annotations

import json
from typing import Any


class ApprovalRecovery:
    def __init__(self, *, store, messages: list[dict[str, Any]], save, gateway) -> None:
        self.store = store
        self.messages = messages
        self.save = save
        self.gateway = gateway

    def consume_one(self, task_id: str) -> bool:
        existing_call_ids = {
            str(item.get("tool_call_id") or "")
            for item in self.messages
            if item.get("role") == "tool"
        }
        repository = self.gateway.repository
        for approval in repository.list_approvals(task_id, limit=1_000):
            if approval.get("status") not in {"approved", "rejected", "expired"}:
                continue
            action_id = str(approval.get("action_id") or "")
            item = repository.get_action(action_id)
            if item is None:
                continue
            action = item["payload"]
            call_id = str(action.get("tool_call_id") or "")
            if not call_id or call_id in existing_call_ids:
                continue
            if approval.get("status") == "approved":
                result = self.gateway.resume_approved(action_id).model_payload
            else:
                expired = approval.get("status") == "expired"
                result = {
                    "ok": False,
                    "status": str(approval.get("status")),
                    "action_id": action_id,
                    "summary": "The high-impact Action approval expired." if expired else "The high-impact Action was rejected.",
                    "error": {
                        "code": "ACTION_APPROVAL_EXPIRED" if expired else "ACTION_REJECTED_BY_USER",
                        "message": "The approval deadline elapsed before a decision." if expired else "The user rejected this high-impact Action.",
                        "retryable": expired,
                    },
                }
                result = self.gateway.resolve_without_execution(
                    action_id, status=str(approval.get("status")), payload=result
                ).model_payload
            self.messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": str(action.get("provider_tool_name") or action.get("capability") or ""),
                "content": json.dumps(result, ensure_ascii=False),
            })
            self.save()
            return True
        return False


__all__ = ["ApprovalRecovery"]
