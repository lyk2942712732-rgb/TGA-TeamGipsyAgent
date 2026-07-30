from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticRepeatDecision:
    previous_action_id: str | None = None
    requires_retry_reason: bool = False


class SemanticRepeatGuard:
    def __init__(self, repository) -> None:
        self.repository = repository

    def check(self, action) -> SemanticRepeatDecision:
        previous = self.repository.find_semantic(action)
        return SemanticRepeatDecision(
            previous_action_id=previous,
            requires_retry_reason=previous is not None,
        )


__all__ = ["SemanticRepeatDecision", "SemanticRepeatGuard"]
