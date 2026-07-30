"""Stable application boundaries implemented by infrastructure adapters."""

from tga.application.ports.gateways import ModelGateway, SchedulerPort, WorkspacePort
from tga.application.ports.repositories import (
    EventRepository,
    EvidenceRepository,
    KnowledgeRepository,
    OrchestrationRepository,
    PlanRepository,
    SessionRepository,
    SolverRepository,
    TaskRepository,
    TranscriptRepository,
)
from tga.application.ports.retrieval import (
    DocumentParser,
    EmbeddingGateway,
    IndexRepository,
    RetrievalGateway,
)

__all__ = [
    "EventRepository",
    "EvidenceRepository",
    "KnowledgeRepository",
    "ModelGateway",
    "OrchestrationRepository",
    "PlanRepository",
    "SchedulerPort",
    "SessionRepository",
    "SolverRepository",
    "TaskRepository",
    "TranscriptRepository",
    "WorkspacePort",
    "DocumentParser",
    "EmbeddingGateway",
    "IndexRepository",
    "RetrievalGateway",
]
