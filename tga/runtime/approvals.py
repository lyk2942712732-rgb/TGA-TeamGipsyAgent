"""Durable expiry handling for schema-v6 governed approvals."""

from __future__ import annotations

from datetime import UTC, datetime

from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.tooling.governance.approvals import SolverApprovalCoordinator


def expire_pending_approvals(
    store, task_id: str, *, now: datetime | None = None
) -> list[str]:
    current_time = now or datetime.now(UTC)
    repositories = PersistenceBundle(store)
    expired: list[str] = []
    for approval in repositories.tool_governance.list_approvals(
        task_id, status="pending", limit=1_000
    ):
        payload = approval["payload"]
        raw_expiry = str(payload.get("expires_at") or "")
        try:
            expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        except ValueError:
            continue
        if expiry > current_time:
            continue
        action_id = str(approval["action_id"])
        action = repositories.tool_governance.get_action(action_id)
        if action is None or action["status"] != "pending_approval":
            continue
        repositories.tool_governance.decide_approval(
            action_id, "expired", expected_status="pending"
        )
        repositories.events.append_agent_event(
            task_id,
            "ACTION_APPROVAL_EXPIRED",
            {
                "action_id": action_id,
                "status": "expired",
                "approval_expires_at": raw_expiry,
            },
            solver_id=str(action["solver_id"]),
            intent_id=str(action["intent_id"] or "") or None,
        )
        SolverApprovalCoordinator(store).resolve(
            solver_id=str(action["solver_id"]),
            intent_id=str(action["intent_id"] or "") or None,
        )
        expired.append(action_id)
    return expired


__all__ = ["expire_pending_approvals"]
