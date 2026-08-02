"""Versioned and bounded event payload contracts."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


CORE_EVENT_TYPES = frozenset({
    "ORCHESTRATOR_STARTED", "SOLVER_CREATED", "SOLVER_STARTED",
    "SOLVER_PAUSED", "SOLVER_COMPLETED", "SOLVER_FAILED",
    "INTENT_CREATED", "INTENT_ASSIGNED", "INTENT_CLAIMED",
    "INTENT_COMPLETED", "WORKER_RESULT_SUBMITTED", "WORKER_RESULT_MERGED",
    "PLAN_UPDATED", "KNOWLEDGE_CANDIDATE_CREATED", "KNOWLEDGE_PROMOTED",
    "KNOWLEDGE_CONFLICT_DETECTED", "EVIDENCE_CLAIM_CREATED",
    "EVIDENCE_CLAIM_REVIEWED", "APPROVAL_REQUESTED", "RETRIEVAL_COMPLETED",
    "TASK_COMPLETION_PROPOSED", "TASK_COMPLETION_ACCEPTED",
})


REQUIRED_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "ORCHESTRATOR_STARTED": frozenset({"supervisor_solver_id"}),
    "SOLVER_CREATED": frozenset({"solver_id", "definition_id", "orchestration_role"}),
    # Solver identity is authoritative in the outer event envelope.
    "SOLVER_STARTED": frozenset(),
    "SOLVER_PAUSED": frozenset({"reason"}),
    "SOLVER_COMPLETED": frozenset(),
    "SOLVER_FAILED": frozenset(),
    "INTENT_CREATED": frozenset({"intent_id"}),
    "INTENT_ASSIGNED": frozenset({"intent_id", "solver_id"}),
    "INTENT_CLAIMED": frozenset({"intent_id", "solver_id"}),
    "INTENT_COMPLETED": frozenset({"intent_id", "status"}),
    "WORKER_RESULT_SUBMITTED": frozenset({"worker_result_id", "intent_id"}),
    "WORKER_RESULT_MERGED": frozenset({"worker_result_id", "intent_id"}),
    "PLAN_UPDATED": frozenset({"operation", "old_version", "new_version"}),
    "KNOWLEDGE_CANDIDATE_CREATED": frozenset({"knowledge_id"}),
    "KNOWLEDGE_PROMOTED": frozenset({"knowledge_id"}),
    "KNOWLEDGE_CONFLICT_DETECTED": frozenset({"conflict_id"}),
    "EVIDENCE_CLAIM_CREATED": frozenset({"evidence_claim_id"}),
    "EVIDENCE_CLAIM_REVIEWED": frozenset({"evidence_claim_id", "status"}),
    "APPROVAL_REQUESTED": frozenset({"approval_id", "action_id"}),
    "RETRIEVAL_COMPLETED": frozenset({"retrieval_run_id", "index_snapshot_id"}),
    "TASK_COMPLETION_PROPOSED": frozenset({"proposal_id"}),
    "TASK_COMPLETION_ACCEPTED": frozenset({"proposal_id"}),
}


class VersionedEventPayload(BaseModel):
    """A transport-safe payload with hard structural and byte bounds.

    ``payload_version`` versions the payload body only.  The surrounding event
    envelope carries its own ``schema_version`` (6); keeping the names distinct
    prevents the two from being read as one version.
    """

    model_config = ConfigDict(extra="allow")
    payload_version: int = Field(default=1, ge=1, le=1_000)

    @model_validator(mode="after")
    def validate_bounds(self) -> "VersionedEventPayload":
        payload = self.model_dump(mode="json")
        _validate_tree(payload)
        if len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")) > 65_536:
            raise ValueError("event payload exceeds 65536 bytes")
        return self


def normalize_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    value = VersionedEventPayload.model_validate({"payload_version": 1, **payload}).model_dump(
        mode="json"
    )
    required = REQUIRED_PAYLOAD_FIELDS.get(event_type)
    if required:
        missing = sorted(required.difference(value))
        if missing:
            raise ValueError(
                f"{event_type} payload is missing required fields: {', '.join(missing)}"
            )
    return value


def _validate_tree(value: Any, *, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("event payload nesting exceeds 8 levels")
    if isinstance(value, dict):
        if len(value) > 128:
            raise ValueError("event payload object exceeds 128 keys")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError("event payload keys must be bounded non-empty strings")
            _validate_tree(item, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 1_024:
            raise ValueError("event payload list exceeds 1024 items")
        for item in value:
            _validate_tree(item, depth=depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("event payload values must be JSON-compatible")


__all__ = [
    "CORE_EVENT_TYPES", "REQUIRED_PAYLOAD_FIELDS", "VersionedEventPayload",
    "normalize_event_payload",
]
