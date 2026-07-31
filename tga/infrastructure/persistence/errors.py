"""Shared deterministic persistence conflict types."""


class PersistenceConflict(RuntimeError):
    """Base class for deterministic persistence conflicts."""


class PlanVersionConflict(PersistenceConflict):
    pass


class IntentClaimConflict(PersistenceConflict):
    pass


class ArtifactImmutableError(PersistenceConflict):
    pass


class OwnershipError(PersistenceConflict):
    pass


class ActionTransitionConflict(PersistenceConflict):
    pass


__all__ = [
    "ArtifactImmutableError",
    "ActionTransitionConflict",
    "IntentClaimConflict",
    "OwnershipError",
    "PersistenceConflict",
    "PlanVersionConflict",
]
