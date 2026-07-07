from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


@dataclass
class TokenBucket:
    rate_per_second: float
    burst: int
    tokens: float = field(init=False)
    updated_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = float(self.burst)
        self.updated_at = monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        now = monotonic()
        elapsed = now - self.updated_at
        self.updated_at = now
        self.tokens = min(float(self.burst), self.tokens + elapsed * self.rate_per_second)
        if self.tokens < cost:
            return False
        self.tokens -= cost
        return True


class RateLimiter:
    def __init__(self, default_rate_per_second: float = 1.0, default_burst: int = 5):
        self.default_rate_per_second = default_rate_per_second
        self.default_burst = default_burst
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        bucket = self._buckets.setdefault(
            key,
            TokenBucket(self.default_rate_per_second, self.default_burst),
        )
        return bucket.allow(cost)

