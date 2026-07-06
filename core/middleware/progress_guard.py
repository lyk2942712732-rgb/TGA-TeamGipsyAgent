"""Budget and no-progress policies."""

from __future__ import annotations

from core.config import CoreConfig
from core.state import Blackboard


def budget_exhausted(board: Blackboard, config: CoreConfig) -> bool:
    return board.step_count >= config.max_steps


def should_reflect(board: Blackboard, config: CoreConfig) -> bool:
    return (
        board.failure_streak >= config.repeat_failure_threshold
        or board.no_progress_count >= config.reflection_threshold
    )
