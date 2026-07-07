"""Minimal rate-limit placeholder."""

from __future__ import annotations

import time


class SimpleRateLimit:
    def __init__(self, delay_s: float = 0.0):
        self.delay_s = delay_s
        self._last = 0.0

    def wait(self) -> None:
        now = time.time()
        remaining = self.delay_s - (now - self._last)
        if remaining > 0:
            time.sleep(remaining)
        self._last = time.time()

