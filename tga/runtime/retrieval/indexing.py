"""Document ingestion and immutable IndexSnapshot construction."""

from __future__ import annotations

import hashlib
import json

from tga.domain.retrieval import (
    CorpusDocument,
    CorpusSource,
    DocumentRevision,
    IndexSnapshot,
    OwnerScope,
)
from tga.evidence.database import utc_now


class RetrievalIndexService:
    def __init__(self, repository, *, parser, embedding_gateway=None) -> None:
        self.repository = repository
        self.parser = parser
        self.embedding_gateway = embedding_gateway

    def ingest(
        self, *, knowledge_base, source, document, revision, raw: bytes,
        chunk_metadata: dict | None = None,
    ):
        """Persist parse success or failure without losing the document revision."""
        self.repository.add_knowledge_base(knowledge_base)
        self.repository.add_source(source)
        existing_document = self.repository.get_document(document.id)
        if existing_document is None:
            self.repository.add_document(document)
            existing_document = document
        elif (
            existing_document.source_id != document.source_id
            or existing_document.knowledge_base_id != document.knowledge_base_id
            or existing_document.owner != document.owner
        ):
            raise PermissionError("CorpusDocument stable ownership changed across revisions")
        parsed_revision, chunks = self.parser.parse(
            document=document, revision=revision, raw=raw, source=source
        )
        if chunk_metadata:
            chunks = tuple(item.model_copy(update={
                "metadata": {**item.metadata, **chunk_metadata}
            }) for item in chunks)
        self.repository.add_revision(parsed_revision)
        self.repository.set_current_revision(
            document.id,
            parsed_revision.id,
            expected_current_revision_id=existing_document.current_revision_id,
        )
        if chunks:
            self.repository.add_chunks(chunks)
        return parsed_revision, chunks

    def ingest_task_artifact(self, *, knowledge_base, artifact, raw: bytes):
        """Project an immutable task Artifact into the task-artifact channel."""
        if knowledge_base.owner.scope != "task" or knowledge_base.owner.task_id != artifact.task_id:
            raise PermissionError("Task Artifact KnowledgeBase owner does not match")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != artifact.sha256:
            raise ValueError("Task Artifact bytes do not match the immutable sha256")
        source = CorpusSource(
            id=f"source_{artifact.id}",
            knowledge_base_id=knowledge_base.id,
            name=f"Task Artifact {artifact.id}",
            kind="uploaded_file",
            channel="task_artifact",
            owner=knowledge_base.owner,
            trust_level="unverified",
            canonical_uri=f"artifact://{artifact.task_id}/{artifact.id}",
            metadata={
                "artifact_id": artifact.id,
                "artifact_kind": artifact.kind,
                "artifact_tool": artifact.tool,
            },
            created_at=artifact.created_at,
        )
        document_id = f"document_{artifact.id}"
        revision_id = f"revision_{artifact.id}_{artifact.sha256[:16]}"
        document = CorpusDocument(
            id=document_id,
            source_id=source.id,
            knowledge_base_id=knowledge_base.id,
            owner=knowledge_base.owner,
            title=artifact.path,
            canonical_uri=source.canonical_uri,
            current_revision_id=revision_id,
            metadata={"artifact_id": artifact.id},
            created_at=artifact.created_at,
        )
        revision = DocumentRevision(
            id=revision_id,
            document_id=document.id,
            source_id=source.id,
            owner=knowledge_base.owner,
            revision=1,
            content_sha256=artifact.sha256,
            extraction_status="pending",
            media_type=artifact.media_type,
            byte_size=len(raw),
            metadata={
                "artifact_id": artifact.id,
                "document_type": "http" if artifact.kind.startswith("http") else None,
            },
            created_at=artifact.created_at,
        )
        return self.ingest(
            knowledge_base=knowledge_base,
            source=source,
            document=document,
            revision=revision,
            raw=raw,
            chunk_metadata={"artifact_id": artifact.id},
        )

    def create_snapshot(
        self,
        *,
        owner: OwnerScope,
        knowledge_base_ids: tuple[str, ...],
        source_ids: tuple[str, ...] = (),
        index_version: int = 1,
    ) -> IndexSnapshot:
        sources = self.repository.list_sources(knowledge_base_ids=knowledge_base_ids)
        if source_ids:
            sources = [item for item in sources if item.id in source_ids]
        selected_source_ids = tuple(item.id for item in sources if item.status == "active")
        documents = self.repository.list_documents(source_ids=selected_source_ids)
        all_chunks = [
            item for item in self.repository.list_chunks()
            if item.source_id in selected_source_ids
        ]
        chunks = []
        document_hashes = {}
        for document in documents:
            document_chunks = [item for item in all_chunks if item.document_id == document.id]
            revisions = [self.repository.get_revision(item.revision_id) for item in document_chunks]
            revisions = [item for item in revisions if item is not None]
            if revisions:
                selected_revision = next(
                    (item for item in revisions if item.id == document.current_revision_id),
                    max(revisions, key=lambda item: item.revision),
                )
                document_hashes[document.id] = selected_revision.content_sha256
                chunks.extend(
                    item for item in document_chunks
                    if item.revision_id == selected_revision.id
                )
        fingerprint = json.dumps({
            "owner": owner.model_dump(mode="json"),
            "knowledge_base_ids": knowledge_base_ids,
            "source_ids": selected_source_ids,
            "document_hashes": document_hashes,
            "chunk_ids": [item.id for item in chunks],
            "parser": self.parser.parser_id,
            "embedding": getattr(self.embedding_gateway, "model", None),
            "index_version": index_version,
        }, sort_keys=True).encode()
        snapshot = IndexSnapshot(
            id=f"snapshot_{hashlib.sha256(fingerprint).hexdigest()[:32]}",
            owner=owner,
            knowledge_base_ids=knowledge_base_ids,
            source_ids=selected_source_ids,
            document_hashes=document_hashes,
            chunk_ids=tuple(item.id for item in chunks),
            chunking_version=self.parser.parser_id,
            embedding_model=getattr(self.embedding_gateway, "model", None),
            index_version=index_version,
            created_at=utc_now(),
        )
        existing = [
            item for item in self.repository.list_snapshots()
            if item.owner == snapshot.owner
            and item.knowledge_base_ids == snapshot.knowledge_base_ids
            and item.source_ids == snapshot.source_ids
            and item.document_hashes == snapshot.document_hashes
            and item.chunk_ids == snapshot.chunk_ids
            and item.chunking_version == snapshot.chunking_version
            and item.embedding_model == snapshot.embedding_model
        ]
        if existing:
            return existing[-1]
        self.repository.save_snapshot(snapshot)
        return snapshot


__all__ = ["RetrievalIndexService"]
