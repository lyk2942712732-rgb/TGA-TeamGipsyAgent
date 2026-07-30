"""Governed Action lifecycle vocabulary and recovery-safe transition service."""

from tga.infrastructure.persistence.errors import (
    ActionTransitionConflict as ActionTransitionError,
)


class GovernedActionService:
    def __init__(self, repository) -> None:
        self.repository = repository

    def propose(self, action) -> None:
        self.repository.add_action(action)

    def transition(self, action_id: str, status: str, *, expected_status: str):
        return self.repository.transition(
            action_id, status, expected_status=expected_status
        )

    def persist_result(self, action_id: str, result) -> None:
        self.repository.save_result(action_id, result)


__all__ = ["ActionTransitionError", "GovernedActionService"]
