"""Application-owned Retrieval ports implemented by local or remote adapters."""

from __future__ import annotations

from typing import Protocol, Sequence

from tga.domain.retrieval import (
    CorpusDocument,
    CorpusSource,
    DocumentChunk,
    DocumentRevision,
    IndexSnapshot,
    IndexBinding,
    KnowledgeBase,
    RetrievalHit,
    RetrievalPolicy,
    RetrievalRequest,
    RetrievalRun,
    RetrievedContextPack,
    OwnerScope,
)


class RetrievalGateway(Protocol):
    def retrieve(
        self, request: RetrievalRequest, policy: RetrievalPolicy
    ) -> RetrievedContextPack: ...


class DocumentParser(Protocol):
    parser_id: str

    def parse(
        self,
        *,
        document: CorpusDocument,
        revision: DocumentRevision,
        raw: bytes,
        source: CorpusSource | None = None,
    ) -> tuple[DocumentRevision, tuple[DocumentChunk, ...]]: ...


class EmbeddingGateway(Protocol):
    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class IndexRepository(Protocol):
    def add_knowledge_base(self, item: KnowledgeBase) -> None: ...
    def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBase | None: ...
    def list_knowledge_bases(self, *, owner: OwnerScope | None = None) -> list[KnowledgeBase]: ...
    def add_source(self, item: CorpusSource) -> None: ...
    def get_source(self, source_id: str) -> CorpusSource | None: ...
    def list_sources(
        self, *, knowledge_base_ids: Sequence[str] | None = None,
        owner: OwnerScope | None = None,
    ) -> list[CorpusSource]: ...
    def add_document(self, item: CorpusDocument) -> None: ...
    def set_current_revision(
        self, document_id: str, revision_id: str,
        *, expected_current_revision_id: str | None,
    ) -> CorpusDocument: ...
    def add_revision(self, item: DocumentRevision) -> None: ...
    def add_chunks(self, items: Sequence[DocumentChunk]) -> None: ...
    def save_snapshot(self, item: IndexSnapshot) -> None: ...
    def get_snapshot(self, snapshot_id: str) -> IndexSnapshot | None: ...
    def get_snapshot_binding(
        self, owner: OwnerScope, purpose: str
    ) -> IndexBinding | None: ...
    def bind_snapshot(
        self, *, owner: OwnerScope, purpose: str, snapshot_id: str,
        expected_snapshot_id: str | None = None, updated_at: str,
    ) -> IndexBinding: ...
    def save_run(self, run: RetrievalRun, hits: Sequence[RetrievalHit]) -> None: ...
    def get_run(self, run_id: str) -> RetrievalRun | None: ...


__all__ = ["DocumentParser", "EmbeddingGateway", "IndexRepository", "RetrievalGateway"]
