"""Composed SQLite evidence store."""

from tga.evidence.database import Database, utc_now
from tga.evidence.repositories import (
    ActionRepository,
    ArtifactRepository,
    ContextMetricRepository,
    EventRepository,
    MemoryRepository,
    RuntimeReadModel,
    SessionRepository,
    StrategyRepository,
    TaskRepository,
)


class EvidenceStore(
    RuntimeReadModel,
    EventRepository,
    ContextMetricRepository,
    StrategyRepository,
    ActionRepository,
    MemoryRepository,
    ArtifactRepository,
    SessionRepository,
    TaskRepository,
    Database,
):
    """Composition root for repository and unit-of-work boundaries."""