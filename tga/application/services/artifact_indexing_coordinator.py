"""Coordinate Artifact ingestion, immutable snapshots, and context binding."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable

from tga.domain.evidence import Artifact
from tga.domain.retrieval import (
    ArtifactIndexProjection,
    ArtifactIndexingPolicy,
    KnowledgeBase,
    OwnerScope,
)
from tga.evidence.database import utc_now
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.infrastructure.retrieval import StructuredDocumentParser
from tga.runtime.retrieval import RetrievalIndexService


class ArtifactIndexingError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ArtifactIndexingCoordinator:
    """Run a resumable, idempotent projection for one task Artifact."""

    def __init__(
        self,
        *,
        repositories,
        raw_loader: Callable[[Artifact], bytes],
        policy: ArtifactIndexingPolicy | None = None,
        parser=None,
        event_repository=None,
    ) -> None:
        self.repositories = repositories
        self.raw_loader = raw_loader
        self.policy = policy or ArtifactIndexingPolicy()
        self.parser = parser or StructuredDocumentParser()
        self.events = event_repository

    def index(
        self,
        artifact: Artifact,
        *,
        task_name: str = "Task",
        solver_id: str | None = None,
    ) -> ArtifactIndexProjection:
        existing = self.repositories.get_artifact_index_projection(artifact.id)
        if existing is not None and existing.artifact_sha256 != artifact.sha256:
            raise ArtifactIndexingError(
                "ARTIFACT_IDENTITY_CONFLICT",
                "Artifact id was previously indexed with a different sha256",
                retryable=False,
            )
        if existing is not None and self._is_current(existing):
            return existing

        created_at = existing.created_at if existing else utc_now()
        attempt = (existing.attempt if existing else 0) + 1
        started = ArtifactIndexProjection(
            artifact_id=artifact.id,
            artifact_sha256=artifact.sha256,
            task_id=artifact.task_id,
            status="indexing",
            source_id=existing.source_id if existing else None,
            document_id=existing.document_id if existing else None,
            revision_id=existing.revision_id if existing else None,
            chunk_ids=existing.chunk_ids if existing else (),
            snapshot_id=existing.snapshot_id if existing else None,
            binding_updated=False,
            attempt=attempt,
            created_at=created_at,
            updated_at=utc_now(),
        )
        self.repositories.save_artifact_index_projection(started)
        self._event(
            artifact, "ARTIFACT_INDEXING_STARTED",
            {"artifact_id": artifact.id, "attempt": attempt},
            solver_id=solver_id,
        )

        try:
            self._validate_policy(artifact)
            raw = self._read(artifact)
            owner = OwnerScope(scope="task", task_id=artifact.task_id)
            knowledge_base = self._knowledge_base(
                owner=owner, task_name=task_name, created_at=created_at
            )
            indexer = RetrievalIndexService(
                self.repositories, parser=self.parser
            )
            revision, chunks = indexer.ingest_task_artifact(
                knowledge_base=knowledge_base, artifact=artifact, raw=raw
            )
            if revision.extraction_status != "parsed" or not chunks:
                raise ArtifactIndexingError(
                    "ARTIFACT_PARSE_FAILED",
                    revision.error or "Artifact parser produced no searchable chunks",
                    retryable=False,
                )
            if len(chunks) > self.policy.max_chunks:
                raise ArtifactIndexingError(
                    "ARTIFACT_CHUNK_LIMIT_EXCEEDED",
                    f"Artifact produced {len(chunks)} chunks; limit is {self.policy.max_chunks}",
                    retryable=False,
                )
            total_tokens = sum(item.token_count for item in chunks)
            if total_tokens > self.policy.max_total_tokens:
                raise ArtifactIndexingError(
                    "ARTIFACT_TOKEN_LIMIT_EXCEEDED",
                    f"Artifact produced {total_tokens} tokens; limit is {self.policy.max_total_tokens}",
                    retryable=False,
                )

            projection = started.model_copy(update={
                "source_id": f"source_{artifact.id}",
                "document_id": f"document_{artifact.id}",
                "revision_id": revision.id,
                "chunk_ids": tuple(item.id for item in chunks),
                "updated_at": utc_now(),
            })
            self.repositories.save_artifact_index_projection(projection)
            snapshot, binding_updated = self._snapshot_and_bind(
                owner=owner,
                knowledge_base_id=knowledge_base.id,
                required_chunk_ids=projection.chunk_ids,
                indexer=indexer,
            )
            completed = projection.model_copy(update={
                "status": "indexed",
                "snapshot_id": snapshot.id,
                "binding_updated": binding_updated,
                "error_code": None,
                "error_message": None,
                "retryable": False,
                "updated_at": utc_now(),
            })
            self.repositories.save_artifact_index_projection(completed)
            self._event(
                artifact,
                "INDEX_SNAPSHOT_CREATED",
                {
                    "artifact_id": artifact.id,
                    "index_snapshot_id": snapshot.id,
                    "index_version": snapshot.index_version,
                    "chunk_count": len(snapshot.chunk_ids),
                },
                solver_id=solver_id,
            )
            if binding_updated:
                self._event(
                    artifact,
                    "INDEX_BINDING_UPDATED",
                    {
                        "artifact_id": artifact.id,
                        "purpose": "context",
                        "index_snapshot_id": snapshot.id,
                    },
                    solver_id=solver_id,
                )
            self._event(
                artifact,
                "ARTIFACT_INDEXED",
                {
                    "artifact_id": artifact.id,
                    "source_id": completed.source_id,
                    "document_id": completed.document_id,
                    "revision_id": completed.revision_id,
                    "chunk_count": len(completed.chunk_ids),
                    "snapshot_id": completed.snapshot_id,
                    "context_binding_updated": completed.binding_updated,
                },
                solver_id=solver_id,
            )
            return completed
        except Exception as exc:
            error = self._classify(exc)
            failed = started.model_copy(update={
                "status": "failed",
                "source_id": f"source_{artifact.id}" if self.repositories.get_source(
                    f"source_{artifact.id}"
                ) else started.source_id,
                "document_id": f"document_{artifact.id}" if self.repositories.get_document(
                    f"document_{artifact.id}"
                ) else started.document_id,
                "revision_id": self._revision_id(artifact),
                "chunk_ids": self._artifact_chunk_ids(artifact.id),
                "error_code": error.code,
                "error_message": str(error)[:4_000],
                "retryable": error.retryable,
                "updated_at": utc_now(),
            })
            self.repositories.save_artifact_index_projection(failed)
            self._event(
                artifact,
                "ARTIFACT_INDEXING_FAILED",
                {
                    "artifact_id": artifact.id,
                    "attempt": attempt,
                    "error_code": error.code,
                    "error_type": type(exc).__name__,
                    "message": str(error)[:1_000],
                    "retryable": error.retryable,
                },
                solver_id=solver_id,
            )
            return failed

    def _validate_policy(self, artifact: Artifact) -> None:
        if not self.policy.enabled or not self.policy.index_on_creation:
            raise ArtifactIndexingError(
                "ARTIFACT_INDEXING_DISABLED", "Artifact indexing is disabled", retryable=False
            )
        if artifact.kind in self.policy.denied_artifact_kinds:
            raise ArtifactIndexingError(
                "ARTIFACT_KIND_DENIED", "Artifact kind is excluded from indexing", retryable=False
            )
        media_type = (
            getattr(artifact, "media_type", None)
            or mimetypes.guess_type(artifact.path)[0]
            or ""
        ).lower()
        if media_type and not (
            media_type.startswith("text/") or media_type in self.policy.allowed_media_types
        ):
            raise ArtifactIndexingError(
                "ARTIFACT_MEDIA_TYPE_DENIED",
                f"Artifact media type is not searchable: {media_type}",
                retryable=False,
            )

    def _read(self, artifact: Artifact) -> bytes:
        try:
            raw = self.raw_loader(artifact)
        except (OSError, ValueError) as exc:
            raise ArtifactIndexingError(
                "ARTIFACT_BYTES_UNAVAILABLE", str(exc), retryable=True
            ) from exc
        if len(raw) > self.policy.max_bytes:
            raise ArtifactIndexingError(
                "ARTIFACT_SIZE_LIMIT_EXCEEDED",
                f"Artifact is {len(raw)} bytes; limit is {self.policy.max_bytes}",
                retryable=False,
            )
        return raw

    def _knowledge_base(
        self, *, owner: OwnerScope, task_name: str, created_at: str
    ) -> KnowledgeBase:
        knowledge_base_id = f"kb_task_artifacts_{owner.task_id}"
        return self.repositories.get_knowledge_base(knowledge_base_id) or KnowledgeBase(
            id=knowledge_base_id,
            name=f"Task Artifacts: {task_name}",
            owner=owner,
            description="Immutable tool outputs indexed as candidate evidence only.",
            created_at=created_at,
        )

    def _snapshot_and_bind(
        self, *, owner, knowledge_base_id, required_chunk_ids, indexer
    ):
        knowledge_base_ids = tuple(sorted({
            item.id for item in self.repositories.list_knowledge_bases()
            if item.status == "active" and (
                item.owner.scope == "global"
                or item.owner.scope == "task" and item.owner.task_id == owner.task_id
            )
        } | {knowledge_base_id}))
        for attempt in range(4):
            current = self.repositories.get_snapshot_binding(owner, "context")
            current_snapshot = (
                self.repositories.get_snapshot(current.index_snapshot_id)
                if current is not None else None
            )
            if current_snapshot is not None and set(required_chunk_ids).issubset(
                current_snapshot.chunk_ids
            ):
                return current_snapshot, True
            versions = [
                item.index_version for item in self.repositories.list_snapshots()
                if item.owner == owner
            ]
            snapshot = indexer.create_snapshot(
                owner=owner,
                knowledge_base_ids=knowledge_base_ids,
                index_version=max(versions, default=0) + 1,
            )
            if not self.policy.auto_refresh_context_binding:
                return snapshot, False
            try:
                self.repositories.bind_snapshot(
                    owner=owner,
                    purpose="context",
                    snapshot_id=snapshot.id,
                    expected_snapshot_id=(current.index_snapshot_id if current else None),
                    updated_at=utc_now(),
                )
                return snapshot, True
            except PersistenceConflict:
                if attempt == 3:
                    raise
        raise AssertionError("unreachable")

    def _is_current(self, projection: ArtifactIndexProjection) -> bool:
        if projection.status != "indexed" or not projection.chunk_ids:
            return False
        owner = OwnerScope(scope="task", task_id=projection.task_id)
        binding = self.repositories.get_snapshot_binding(owner, "context")
        if binding is None:
            return not self.policy.auto_refresh_context_binding and bool(projection.snapshot_id)
        snapshot = self.repositories.get_snapshot(binding.index_snapshot_id)
        return snapshot is not None and set(projection.chunk_ids).issubset(snapshot.chunk_ids)

    def _artifact_chunk_ids(self, artifact_id: str) -> tuple[str, ...]:
        return tuple(
            item.id for item in self.repositories.list_chunks()
            if item.metadata.get("artifact_id") == artifact_id
        )

    def _revision_id(self, artifact: Artifact) -> str | None:
        revision_id = f"revision_{artifact.id}_{artifact.sha256[:16]}"
        return revision_id if self.repositories.get_revision(revision_id) else None

    @staticmethod
    def _classify(exc: Exception) -> ArtifactIndexingError:
        if isinstance(exc, ArtifactIndexingError):
            return exc
        if isinstance(exc, PersistenceConflict):
            return ArtifactIndexingError(
                "ARTIFACT_INDEX_CONFLICT", str(exc), retryable=True
            )
        if isinstance(exc, PermissionError):
            return ArtifactIndexingError(
                "ARTIFACT_OWNER_MISMATCH", str(exc), retryable=False
            )
        if isinstance(exc, ValueError):
            return ArtifactIndexingError(
                "ARTIFACT_INTEGRITY_ERROR", str(exc), retryable=False
            )
        return ArtifactIndexingError(
            "ARTIFACT_INDEXING_ERROR", str(exc), retryable=True
        )

    def _event(self, artifact, event_type, payload, *, solver_id) -> None:
        if self.events is not None:
            self.events.append_agent_event(
                artifact.task_id,
                event_type,
                payload,
                solver_id=solver_id,
                intent_id=artifact.intent_id,
            )


__all__ = ["ArtifactIndexingCoordinator", "ArtifactIndexingError"]
