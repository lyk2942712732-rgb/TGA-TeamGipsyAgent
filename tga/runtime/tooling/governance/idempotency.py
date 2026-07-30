from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IdempotencyReservation:
    created: bool
    action_id: str
    result: dict[str, Any] | None = None


class IdempotencyService:
    def __init__(self, repository) -> None:
        self.repository = repository

    def reserve(self, action) -> IdempotencyReservation:
        created, action_id, result = self.repository.reserve_idempotency(action)
        return IdempotencyReservation(created, action_id, result)

    def lookup(self, action) -> IdempotencyReservation | None:
        existing = self.repository.lookup_idempotency(action.idempotency_key)
        if existing is None:
            return None
        return IdempotencyReservation(
            created=False,
            action_id=existing["action_id"],
            result=existing["result"],
        )


__all__ = ["IdempotencyReservation", "IdempotencyService"]
