"""Process-local model request slots shared by one task run."""

from __future__ import annotations

import threading
from typing import Callable, TypeVar


T = TypeVar("T")


class ModelCallLimiter:
    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("model call capacity must be positive")
        self._semaphore = threading.BoundedSemaphore(capacity)

    def run(self, operation: Callable[[], T]) -> T:
        with self._semaphore:
            return operation()


__all__ = ["ModelCallLimiter"]
