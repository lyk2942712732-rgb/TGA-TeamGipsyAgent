"""Durable expiry handling for pending high-impact actions."""

from __future__ import annotations

from datetime import UTC, datetime

from tga.contracts import ActionResult, TGAError
from tga.runtime.coordinator import SessionCoordinator


def expire_pending_approvals(store, task_id: str, *, now: datetime | None = None) -> list[str]:
    """Reject elapsed approvals and resume the Session with a tool result.

    The ReAct runner consumes the rejected ActionSpec on its next turn, so an
    expiry has the same durable transcript semantics as an explicit rejection.
    """
    current_time = now or datetime.now(UTC)
    expired: list[str] = []
    with store.transaction():
        session = store.get_session(task_id)
        if session is None or session.status != "awaiting_approval":
            return expired
        for action in store.list_actions(task_id):
            if action.get("status") != "pending_approval":
                continue
            raw_expiry = str(action.get("approval_expires_at") or "")
            try:
                expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expiry > current_time:
                continue
            action_id = str(action["id"])
            store.update_action_status(action_id, "rejected", expected_status="pending_approval")
            store.add_action_result(ActionResult(
                action_id=action_id,
                task_id=task_id,
                solver_id=str(action.get("solver_id") or ""),
                status="rejected",
                summary="High-impact action approval expired without a decision.",
                error=TGAError(
                    code="ACTION_APPROVAL_EXPIRED",
                    message="The approval deadline elapsed before the user made a decision.",
                    retryable=True,
                ),
            ))
            store.append_agent_event(
                task_id,
                "ACTION_APPROVAL_EXPIRED",
                {"action_id": action_id, "status": "rejected", "approval_expires_at": raw_expiry},
                solver_id=str(action.get("solver_id") or "") or None,
            )
            SessionCoordinator(store).resume(
                task_id=task_id,
                reason=f"action_approval_expired:{action_id}",
            )
            expired.append(action_id)
            break
    return expired
