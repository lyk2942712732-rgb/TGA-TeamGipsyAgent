"""Stable retrieval port reserved for a future external knowledge base."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class RAGQuery:
    task_id: str
    mode: str
    text: str
    limit: int = 6


@dataclass(frozen=True)
class RAGChunk:
    id: str
    content: str
    source: str
    score: float
    metadata: dict[str, str]


class RAGRetriever(Protocol):
    retriever_id: str

    def retrieve(self, query: RAGQuery) -> Sequence[RAGChunk]: ...


class NullRAGRetriever:
    """Explicit no-op used until a knowledge backend is configured."""

    retriever_id = "rag-disabled-v1"

    def retrieve(self, query: RAGQuery) -> Sequence[RAGChunk]:
        del query
        return ()

