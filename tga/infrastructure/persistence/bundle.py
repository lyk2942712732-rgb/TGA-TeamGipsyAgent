"""Composition-friendly owner for one schema-v6 SQLite connection."""

from __future__ import annotations

from pathlib import Path

from tga.evidence.database import Database
from tga.infrastructure.persistence.repositories import (
    SqliteEvidenceRepository,
    SqliteEventRepository,
    SqliteKnowledgeRepository,
    SqliteOrchestrationRepository,
    SqlitePlanRepository,
    SqliteSolverRepository,
    SqliteTaskRepository,
    SqliteTranscriptRepository,
)
from tga.infrastructure.persistence.tool_governance import SqliteToolGovernanceRepository
from tga.infrastructure.persistence.retrieval import SqliteRetrievalRepository


class PersistenceBundle:
    def __init__(self, database: Database):
        self.database = database
        self.tasks = SqliteTaskRepository(database)
        self.solvers = SqliteSolverRepository(database)
        self.plans = SqlitePlanRepository(database)
        self.knowledge = SqliteKnowledgeRepository(database)
        self.evidence = SqliteEvidenceRepository(database)
        self.transcripts = SqliteTranscriptRepository(database)
        self.events = SqliteEventRepository(database)
        self.orchestration = SqliteOrchestrationRepository(database)
        self.tool_governance = SqliteToolGovernanceRepository(database)
        self.retrieval = SqliteRetrievalRepository(database)

    @classmethod
    def open(cls, db_path: str | Path) -> "PersistenceBundle":
        return cls(Database(db_path))

    def transaction(self):
        return self.database.transaction()

    def close(self) -> None:
        self.database.close()


__all__ = ["PersistenceBundle"]
