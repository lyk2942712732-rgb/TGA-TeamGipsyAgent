"""Composed SQLite evidence store."""

from tga.evidence.database import Database, utc_now
from tga.evidence.repositories import (
    ArtifactRepository,
    ContextMetricRepository,
    EventRepository,
    SessionRepository,
    TaskRepository,
)


class EvidenceStore(
    EventRepository,
    ContextMetricRepository,
    ArtifactRepository,
    SessionRepository,
    TaskRepository,
    Database,
):
    """Composition root for repository and unit-of-work boundaries."""
