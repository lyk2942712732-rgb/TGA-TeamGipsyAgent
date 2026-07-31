"""Hierarchical durable Task/Solver/Intent budget manager."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class NetworkPermit(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    idempotency_key: str
    task_id: str
    solver_id: str
    intent_id: str | None = None
    status: str
    acquired_at: str
    expires_at: str


class NetworkBudgetLimiter:
    """Durable Task-scoped network concurrency and sliding-window limiter."""

    def __init__(self, repository) -> None:
        self.repository = repository

    def acquire(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        solver_id: str,
        intent_id: str | None = None,
        ttl_seconds: float = 120,
    ) -> NetworkPermit:
        return NetworkPermit.model_validate(self.repository.acquire_network_permit(
            idempotency_key=idempotency_key,
            task_id=task_id,
            solver_id=solver_id,
            intent_id=intent_id,
            ttl_seconds=ttl_seconds,
        ))

    def release(self, permit: NetworkPermit) -> bool:
        return self.repository.release_network_permit(permit.idempotency_key)


class BudgetManager:
    def __init__(self, repository) -> None:
        self.repository = repository

    def record_usage(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        solver_id: str,
        intent_id: str | None = None,
        turns: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        artifact_bytes: int = 0,
        network_requests: int = 0,
    ) -> dict[str, Any]:
        return self.repository.record_runtime_usage(
            idempotency_key=idempotency_key,
            task_id=task_id,
            solver_id=solver_id,
            intent_id=intent_id,
            turns=turns,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            artifact_bytes=artifact_bytes,
            network_requests=network_requests,
        )

    def reserve_model_tokens(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.reserve_model_tokens(**kwargs)

    def settle_model_tokens(self, reservation_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.repository.settle_model_tokens(reservation_id, **kwargs)

    def release_model_tokens(self, reservation_id: str) -> bool:
        return self.repository.release_model_tokens(reservation_id)


__all__ = ["BudgetManager", "NetworkBudgetLimiter", "NetworkPermit"]
