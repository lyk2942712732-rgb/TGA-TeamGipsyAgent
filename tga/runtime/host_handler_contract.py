"""Host handler keys implemented by the production runtime."""

from __future__ import annotations

from tga.application.capabilities import HostCapabilityRegistry


RUNTIME_HOST_HANDLER_KEYS = frozenset({
    "artifact.inspect",
    "artifact.list",
    "artifact.publish",
    "evidence.confirm_finding",
    "evidence.inspect",
    "knowledge.inspect",
    "knowledge.propose",
    "orchestration.create_intent",
    "orchestration.inspect_task_state",
    "orchestration.spawn_solver",
    "orchestration.update_global_plan",
    "orchestration.update_local_plan",
    "reporting.confirmed_evidence",
    "reporting.confirmed_findings",
    "reporting.confirmed_knowledge",
    "reporting.request_report",
    "reporting.write",
    "result.inspect_worker_result",
    "result.propose_task_completion",
    "result.submit_worker_result",
    "retrieval.search",
    "review.evidence",
    "review.finding",
    "review.request_more_evidence",
    "review.request_review",
    "task_input.get",
    "task_input.list",
    "task_input.materialize",
    "task_input.read",
    "task_input.search",
    "task_input.view",
})


def validate_runtime_host_handlers(
    registry: HostCapabilityRegistry | None = None,
) -> None:
    (registry or HostCapabilityRegistry()).validate_handlers(
        RUNTIME_HOST_HANDLER_KEYS
    )


__all__ = ["RUNTIME_HOST_HANDLER_KEYS", "validate_runtime_host_handlers"]
