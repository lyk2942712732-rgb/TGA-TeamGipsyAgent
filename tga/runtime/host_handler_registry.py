"""Canonical Host handler contract and per-session registrations."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from tga.application.capabilities import HostCapabilityRegistry


# This is the one contract used by startup validation, API projections, and
# session registration. The values are handler keys, not provider tool names.
DECLARED_HOST_HANDLER_KEYS = frozenset({
    "artifact.inspect", "artifact.list", "artifact.publish",
    "evidence.confirm_finding", "evidence.inspect", "knowledge.inspect",
    "knowledge.propose", "orchestration.create_intent",
    "orchestration.inspect_task_state", "orchestration.spawn_solver",
    "orchestration.update_global_plan", "orchestration.update_local_plan",
    "reporting.confirmed_evidence", "reporting.confirmed_findings",
    "reporting.confirmed_knowledge", "reporting.request_report",
    "reporting.write", "result.inspect_worker_result",
    "result.propose_task_completion", "result.submit_worker_result",
    "retrieval.search", "review.evidence", "review.finding",
    "review.request_more_evidence", "review.request_review", "task_input.get",
    "task_input.list", "task_input.materialize", "task_input.read",
    "task_input.search", "task_input.view",
})


class HostHandlerRegistry:
    def __init__(
        self,
        *,
        host_registry: HostCapabilityRegistry | None = None,
        host_capabilities: Iterable | None = None,
    ) -> None:
        self.host_registry = host_registry or HostCapabilityRegistry()
        self._handler_keys = {
            item.id: item.handler_key for item in (host_capabilities or ())
        }
        self._handlers: dict[str, Callable] = {}

    @classmethod
    def contract(cls) -> frozenset[str]:
        return DECLARED_HOST_HANDLER_KEYS

    def register(self, capability_id: str, handler: Callable) -> None:
        self._handlers[self._handler_key(capability_id)] = handler

    def register_many(
        self, capabilities: Iterable[str], handler: Callable
    ) -> None:
        for capability_id in capabilities:
            self.register(capability_id, handler)

    def resolve(self, capability_id: str) -> Callable:
        try:
            return self._handlers[self._handler_key(capability_id)]
        except KeyError as exc:
            raise KeyError(
                f"Host capability has no registered runtime handler: {capability_id}"
            ) from exc

    def execute(self, capability_id: str, request):
        return self.resolve(capability_id)(request)

    def missing(self, capabilities: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted(
            capability_id
            for capability_id in capabilities
            if self._handler_key(capability_id) not in self._handlers
        ))

    def _handler_key(self, capability_id: str) -> str:
        if capability_id in self._handler_keys:
            return self._handler_keys[capability_id]
        return self.host_registry.require(capability_id).handler_key

    def has_contract(self, handler_key: str) -> bool:
        return handler_key in self.contract()

    def validate_contract(self) -> None:
        missing = sorted(
            item.handler_key
            for item in self.host_registry.all()
            if item.handler_key not in self.contract()
        )
        if missing:
            raise RuntimeError(f"Host capabilities missing handler contract: {missing}")


def validate_runtime_host_handlers(
    registry: HostCapabilityRegistry | None = None,
) -> None:
    HostHandlerRegistry(host_registry=registry).validate_contract()


__all__ = [
    "DECLARED_HOST_HANDLER_KEYS",
    "HostHandlerRegistry",
    "validate_runtime_host_handlers",
]
