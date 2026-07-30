class ResourceLockService:
    def __init__(self, repository) -> None:
        self.repository = repository

    def acquire(self, action, *, ttl_seconds: float = 60) -> bool:
        return self.repository.acquire_lock(action, ttl_seconds=ttl_seconds)

    def release(self, action) -> None:
        self.repository.release_lock(action)


__all__ = ["ResourceLockService"]
