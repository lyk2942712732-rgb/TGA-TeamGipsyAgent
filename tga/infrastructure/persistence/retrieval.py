"""SQLite retrieval repository with scope-neutral ownership projections."""

from __future__ import annotations

import sqlite3
import hashlib
import json
from collections.abc import Iterable
from typing import Any, TypeVar

from pydantic import BaseModel

from tga.domain.retrieval import (
    ArtifactIndexProjection,
    CorpusDocument,
    CorpusSource,
    DocumentChunk,
    DocumentRevision,
    IndexSnapshot,
    IndexBinding,
    KnowledgeBase,
    OwnerScope,
    RetrievalHit,
    RetrievalRun,
)
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.domain.skills import SkillPublication


ModelT = TypeVar("ModelT", bound=BaseModel)


def _owner_columns(owner: OwnerScope) -> tuple[str, str | None, str | None, str | None]:
    return owner.scope, owner.workspace_id, owner.task_id, owner.solver_id


class SqliteRetrievalRepository:
    def __init__(self, database) -> None:
        self.database = database
        self.conn = database.conn

    def save_artifact_index_projection(self, item: ArtifactIndexProjection) -> None:
        artifact = self.conn.execute(
            "SELECT task_id,payload_json FROM artifacts WHERE id=?",
            (item.artifact_id,),
        ).fetchone()
        if artifact is None or artifact["task_id"] != item.task_id:
            raise PersistenceConflict("ArtifactIndexProjection Artifact ownership is invalid")
        if str(json.loads(artifact["payload_json"])["sha256"]) != item.artifact_sha256:
            raise PersistenceConflict("ArtifactIndexProjection immutable sha256 changed")
        cursor = self.conn.execute(
            "INSERT INTO artifact_index_projections(artifact_id,task_id,artifact_sha256,status,attempt,snapshot_id,binding_updated,retryable,payload_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(artifact_id) DO UPDATE SET "
            "status=excluded.status,attempt=excluded.attempt,snapshot_id=excluded.snapshot_id,"
            "binding_updated=excluded.binding_updated,retryable=excluded.retryable,"
            "payload_json=excluded.payload_json,updated_at=excluded.updated_at "
            "WHERE artifact_index_projections.task_id=excluded.task_id "
            "AND artifact_index_projections.artifact_sha256=excluded.artifact_sha256",
            (
                item.artifact_id, item.task_id, item.artifact_sha256, item.status,
                item.attempt, item.snapshot_id, int(item.binding_updated),
                int(item.retryable), item.model_dump_json(), item.created_at,
                item.updated_at,
            ),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict("ArtifactIndexProjection immutable identity changed")
        self.database._commit()

    def get_artifact_index_projection(
        self, artifact_id: str
    ) -> ArtifactIndexProjection | None:
        row = self.conn.execute(
            "SELECT payload_json FROM artifact_index_projections WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        return self._one(row, ArtifactIndexProjection)

    def list_artifact_index_projections(
        self, task_id: str
    ) -> list[ArtifactIndexProjection]:
        rows = self.conn.execute(
            "SELECT payload_json FROM artifact_index_projections WHERE task_id=? "
            "ORDER BY created_at,artifact_id",
            (task_id,),
        ).fetchall()
        return [ArtifactIndexProjection.model_validate_json(row["payload_json"]) for row in rows]

    def _add_immutable(
        self,
        *,
        table: str,
        identifier: str,
        payload: str,
        sql: str,
        parameters: tuple[Any, ...],
    ) -> None:
        current = self.conn.execute(
            f"SELECT payload_json FROM {table} WHERE id=?", (identifier,)
        ).fetchone()
        if current is not None:
            if current["payload_json"] != payload:
                raise PersistenceConflict(f"immutable retrieval record changed: {identifier}")
            return
        try:
            self.conn.execute(sql, parameters)
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(
                f"retrieval record violates ownership or revision constraints: {identifier}"
            ) from exc
        self.database._commit()

    @staticmethod
    def _one(row, model: type[ModelT]) -> ModelT | None:
        return model.model_validate_json(row["payload_json"]) if row else None

    def add_knowledge_base(self, item: KnowledgeBase) -> None:
        payload = item.model_dump_json()
        owner = _owner_columns(item.owner)
        self._add_immutable(
            table="knowledge_bases", identifier=item.id, payload=payload,
            sql=(
                "INSERT INTO knowledge_bases(id,owner_scope,workspace_id,task_id,solver_id,status,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)"
            ),
            parameters=(item.id, *owner, item.status, payload, item.created_at, item.updated_at),
        )

    def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBase | None:
        return self._one(self.conn.execute(
            "SELECT payload_json FROM knowledge_bases WHERE id=?", (knowledge_base_id,)
        ).fetchone(), KnowledgeBase)

    def list_knowledge_bases(self, *, owner: OwnerScope | None = None) -> list[KnowledgeBase]:
        values = [KnowledgeBase.model_validate_json(row["payload_json"]) for row in self.conn.execute(
            "SELECT payload_json FROM knowledge_bases ORDER BY created_at,id"
        ).fetchall()]
        return [item for item in values if owner is None or item.owner == owner]

    def add_source(self, item: CorpusSource) -> None:
        knowledge_base = self.get_knowledge_base(item.knowledge_base_id)
        if knowledge_base is None:
            raise PersistenceConflict("CorpusSource KnowledgeBase is missing")
        if not self._source_owner_within_knowledge_base(
            item.owner, knowledge_base.owner
        ):
            raise PersistenceConflict("CorpusSource owner is outside its KnowledgeBase")
        payload = item.model_dump_json()
        owner = _owner_columns(item.owner)
        self._add_immutable(
            table="corpus_sources", identifier=item.id, payload=payload,
            sql=(
                "INSERT INTO corpus_sources(id,knowledge_base_id,owner_scope,workspace_id,task_id,solver_id,kind,channel,trust_level,status,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            ),
            parameters=(
                item.id, item.knowledge_base_id, *owner, item.kind, item.channel,
                item.trust_level, item.status, payload, item.created_at, item.updated_at,
            ),
        )

    def get_source(self, source_id: str) -> CorpusSource | None:
        return self._one(self.conn.execute(
            "SELECT payload_json FROM corpus_sources WHERE id=?", (source_id,)
        ).fetchone(), CorpusSource)

    def list_sources(
        self, *, knowledge_base_ids: Iterable[str] | None = None,
        owner: OwnerScope | None = None,
    ) -> list[CorpusSource]:
        ids = tuple(knowledge_base_ids or ())
        if ids:
            placeholders = ",".join("?" for _ in ids)
            rows = self.conn.execute(
                f"SELECT payload_json FROM corpus_sources WHERE knowledge_base_id IN ({placeholders}) ORDER BY created_at,id",
                ids,
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT payload_json FROM corpus_sources ORDER BY created_at,id"
            ).fetchall()
        values = [CorpusSource.model_validate_json(row["payload_json"]) for row in rows]
        return [item for item in values if owner is None or item.owner == owner]

    def add_document(self, item: CorpusDocument) -> None:
        source = self.get_source(item.source_id)
        if (
            source is None
            or source.knowledge_base_id != item.knowledge_base_id
            or source.owner != item.owner
        ):
            raise PersistenceConflict("CorpusDocument source ownership is invalid")
        payload = item.model_dump_json()
        owner = _owner_columns(item.owner)
        self._add_immutable(
            table="corpus_documents", identifier=item.id, payload=payload,
            sql=(
                "INSERT INTO corpus_documents(id,source_id,knowledge_base_id,owner_scope,workspace_id,task_id,solver_id,current_revision_id,status,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            ),
            parameters=(
                item.id, item.source_id, item.knowledge_base_id, *owner,
                item.current_revision_id, item.status, payload, item.created_at, item.updated_at,
            ),
        )

    def get_document(self, document_id: str) -> CorpusDocument | None:
        return self._one(self.conn.execute(
            "SELECT payload_json FROM corpus_documents WHERE id=?", (document_id,)
        ).fetchone(), CorpusDocument)

    def set_current_revision(
        self,
        document_id: str,
        revision_id: str,
        *,
        expected_current_revision_id: str | None,
    ) -> CorpusDocument:
        current = self.get_document(document_id)
        revision = self.get_revision(revision_id)
        if current is None or revision is None or revision.document_id != document_id:
            raise PersistenceConflict("current DocumentRevision ancestry is invalid")
        if current.current_revision_id == revision_id:
            return current
        replacement = current.model_copy(update={
            "current_revision_id": revision_id,
            "updated_at": revision.created_at,
        })
        cursor = self.conn.execute(
            "UPDATE corpus_documents SET current_revision_id=?,payload_json=?,updated_at=? "
            "WHERE id=? AND current_revision_id IS ?",
            (
                revision_id, replacement.model_dump_json(), replacement.updated_at,
                document_id, expected_current_revision_id,
            ),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict("CorpusDocument current revision changed concurrently")
        self.database._commit()
        return replacement

    def list_documents(
        self, *, source_ids: Iterable[str] | None = None,
        owner: OwnerScope | None = None,
    ) -> list[CorpusDocument]:
        ids = tuple(source_ids or ())
        if ids:
            placeholders = ",".join("?" for _ in ids)
            rows = self.conn.execute(
                f"SELECT payload_json FROM corpus_documents WHERE source_id IN ({placeholders}) ORDER BY created_at,id",
                ids,
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT payload_json FROM corpus_documents ORDER BY created_at,id"
            ).fetchall()
        values = [CorpusDocument.model_validate_json(row["payload_json"]) for row in rows]
        return [item for item in values if owner is None or item.owner == owner]

    def add_revision(self, item: DocumentRevision) -> None:
        document = self.get_document(item.document_id)
        if (
            document is None
            or document.source_id != item.source_id
            or document.owner != item.owner
        ):
            raise PersistenceConflict("DocumentRevision document ownership is invalid")
        payload = item.model_dump_json()
        owner = _owner_columns(item.owner)
        self._add_immutable(
            table="document_revisions", identifier=item.id, payload=payload,
            sql=(
                "INSERT INTO document_revisions(id,document_id,source_id,owner_scope,workspace_id,task_id,solver_id,revision,content_sha256,extraction_status,payload_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            ),
            parameters=(
                item.id, item.document_id, item.source_id, *owner, item.revision,
                item.content_sha256, item.extraction_status, payload, item.created_at,
            ),
        )

    def get_revision(self, revision_id: str) -> DocumentRevision | None:
        return self._one(self.conn.execute(
            "SELECT payload_json FROM document_revisions WHERE id=?", (revision_id,)
        ).fetchone(), DocumentRevision)

    def list_revisions(self, document_id: str) -> list[DocumentRevision]:
        rows = self.conn.execute(
            "SELECT payload_json FROM document_revisions WHERE document_id=? "
            "ORDER BY revision,id",
            (document_id,),
        ).fetchall()
        return [DocumentRevision.model_validate_json(row["payload_json"]) for row in rows]

    def add_chunks(self, items: Iterable[DocumentChunk]) -> None:
        with self.database.transaction():
            for item in items:
                document = self.get_document(item.document_id)
                revision = self.get_revision(item.revision_id)
                source = self.get_source(item.source_id)
                if (
                    document is None or revision is None or source is None
                    or document.source_id != item.source_id
                    or revision.document_id != item.document_id
                    or source.knowledge_base_id != item.knowledge_base_id
                ):
                    raise PersistenceConflict("DocumentChunk ancestry is invalid")
                payload = item.model_dump_json()
                owner = _owner_columns(item.owner)
                self._add_immutable(
                    table="document_chunks", identifier=item.id, payload=payload,
                    sql=(
                        "INSERT INTO document_chunks(id,knowledge_base_id,source_id,document_id,revision_id,owner_scope,workspace_id,task_id,solver_id,channel,trust_level,content,token_count,payload_json,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    ),
                    parameters=(
                        item.id, item.knowledge_base_id, item.source_id,
                        item.document_id, item.revision_id, *owner, item.channel,
                        item.trust_level, item.content, item.token_count, payload,
                        item.created_at,
                    ),
                )

    def get_chunk(self, chunk_id: str) -> DocumentChunk | None:
        return self._one(self.conn.execute(
            "SELECT payload_json FROM document_chunks WHERE id=?", (chunk_id,)
        ).fetchone(), DocumentChunk)

    def list_chunks(self, chunk_ids: Iterable[str] | None = None) -> list[DocumentChunk]:
        ids = tuple(chunk_ids or ())
        if not ids:
            rows = self.conn.execute(
                "SELECT payload_json FROM document_chunks ORDER BY created_at,id"
            ).fetchall()
            return [DocumentChunk.model_validate_json(row["payload_json"]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT id,payload_json FROM document_chunks WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        values = {row["id"]: DocumentChunk.model_validate_json(row["payload_json"]) for row in rows}
        return [values[item_id] for item_id in ids if item_id in values]

    def save_snapshot(self, item: IndexSnapshot) -> None:
        missing_chunks = [item_id for item_id in item.chunk_ids if self.get_chunk(item_id) is None]
        if missing_chunks:
            raise PersistenceConflict(f"IndexSnapshot chunks are missing: {missing_chunks[:3]}")
        payload = item.model_dump_json()
        owner = _owner_columns(item.owner)
        self._add_immutable(
            table="index_snapshots", identifier=item.id, payload=payload,
            sql=(
                "INSERT INTO index_snapshots(id,owner_scope,workspace_id,task_id,solver_id,index_version,payload_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)"
            ),
            parameters=(item.id, *owner, item.index_version, payload, item.created_at),
        )

    def get_snapshot(self, snapshot_id: str) -> IndexSnapshot | None:
        return self._one(self.conn.execute(
            "SELECT payload_json FROM index_snapshots WHERE id=?", (snapshot_id,)
        ).fetchone(), IndexSnapshot)

    def list_snapshots(self, *, owner: OwnerScope | None = None) -> list[IndexSnapshot]:
        values = [IndexSnapshot.model_validate_json(row["payload_json"]) for row in self.conn.execute(
            "SELECT payload_json FROM index_snapshots ORDER BY created_at,index_version,id"
        ).fetchall()]
        return [item for item in values if owner is None or item.owner == owner]

    def get_snapshot_binding(
        self, owner: OwnerScope, purpose: str
    ) -> IndexBinding | None:
        values = _owner_columns(owner)
        row = self.conn.execute(
            "SELECT payload_json FROM index_bindings WHERE owner_scope=? "
            "AND workspace_id IS ? AND task_id IS ? AND solver_id IS ? AND purpose=?",
            (*values, purpose),
        ).fetchone()
        return self._one(row, IndexBinding)

    def bind_snapshot(
        self,
        *,
        owner: OwnerScope,
        purpose: str,
        snapshot_id: str,
        expected_snapshot_id: str | None = None,
        updated_at: str,
    ) -> IndexBinding:
        if self.get_snapshot(snapshot_id) is None:
            raise PersistenceConflict("IndexBinding snapshot is missing")
        with self.database.transaction():
            current = self.get_snapshot_binding(owner, purpose)
            if current is None:
                if expected_snapshot_id is not None:
                    raise PersistenceConflict("IndexBinding was not previously established")
                owner_key = ":".join(
                    value or "-" for value in _owner_columns(owner)
                )
                item = IndexBinding(
                    id=(
                        "binding_"
                        + hashlib.sha256(f"{owner_key}:{purpose}".encode()).hexdigest()[:32]
                    ),
                    owner=owner,
                    purpose=purpose,
                    index_snapshot_id=snapshot_id,
                    version=1,
                    created_at=updated_at,
                    updated_at=updated_at,
                )
                self.conn.execute(
                    "INSERT INTO index_bindings(id,owner_scope,workspace_id,task_id,solver_id,purpose,index_snapshot_id,version,payload_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item.id, *_owner_columns(owner), purpose, snapshot_id,
                        item.version, item.model_dump_json(), updated_at, updated_at,
                    ),
                )
                return item
            if current.index_snapshot_id == snapshot_id:
                return current
            if current.index_snapshot_id != expected_snapshot_id:
                raise PersistenceConflict("IndexBinding changed concurrently")
            replacement = current.model_copy(update={
                "index_snapshot_id": snapshot_id,
                "version": current.version + 1,
                "updated_at": updated_at,
            })
            cursor = self.conn.execute(
                "UPDATE index_bindings SET index_snapshot_id=?,version=version+1,payload_json=?,updated_at=? "
                "WHERE id=? AND version=? AND index_snapshot_id=?",
                (
                    snapshot_id, replacement.model_dump_json(), updated_at,
                    current.id, current.version, expected_snapshot_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflict("IndexBinding changed concurrently")
            return replacement

    def save_run(self, run: RetrievalRun, hits: Iterable[RetrievalHit]) -> None:
        if self.get_snapshot(run.index_snapshot_id) is None:
            raise PersistenceConflict("RetrievalRun IndexSnapshot is missing")
        with self.database.transaction():
            payload = run.model_dump_json()
            owner = _owner_columns(run.owner)
            self._add_immutable(
                table="retrieval_runs", identifier=run.id, payload=payload,
                sql=(
                    "INSERT INTO retrieval_runs(id,owner_scope,workspace_id,task_id,solver_id,intent_id,index_snapshot_id,requested_method,method,query,payload_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                ),
                parameters=(
                    run.id, *owner, run.intent_id, run.index_snapshot_id,
                    run.requested_method, run.method, run.query, payload, run.created_at,
                ),
            )
            for hit in hits:
                if hit.retrieval_run_id != run.id or self.get_chunk(hit.chunk_id) is None:
                    raise PersistenceConflict("RetrievalHit ancestry is invalid")
                self._add_immutable(
                    table="retrieval_hits", identifier=hit.id,
                    payload=hit.model_dump_json(),
                    sql=(
                        "INSERT INTO retrieval_hits(id,retrieval_run_id,owner_scope,workspace_id,task_id,solver_id,chunk_id,rank,selected_for_context,payload_json,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                    ),
                    parameters=(
                        hit.id, hit.retrieval_run_id, *_owner_columns(hit.owner),
                        hit.chunk_id, hit.rank,
                        int(hit.selected_for_context), hit.model_dump_json(), hit.created_at,
                    ),
                )

    def get_run(self, run_id: str) -> RetrievalRun | None:
        return self._one(self.conn.execute(
            "SELECT payload_json FROM retrieval_runs WHERE id=?", (run_id,)
        ).fetchone(), RetrievalRun)

    def list_runs(
        self, *, task_id: str | None = None, solver_id: str | None = None,
        owner: OwnerScope | None = None, limit: int = 200,
    ) -> list[RetrievalRun]:
        rows = self.conn.execute(
            "SELECT payload_json FROM retrieval_runs "
            "WHERE (? IS NULL OR task_id=?) AND (? IS NULL OR solver_id=?) "
            "ORDER BY created_at DESC,id DESC LIMIT ?",
            (task_id, task_id, solver_id, solver_id, max(1, min(limit, 1_000))),
        ).fetchall()
        values = [RetrievalRun.model_validate_json(row["payload_json"]) for row in rows]
        return [item for item in values if owner is None or item.owner == owner]

    def get_hit(self, hit_id: str) -> RetrievalHit | None:
        return self._one(self.conn.execute(
            "SELECT payload_json FROM retrieval_hits WHERE id=?", (hit_id,)
        ).fetchone(), RetrievalHit)

    def list_hits(self, run_id: str) -> list[RetrievalHit]:
        return [RetrievalHit.model_validate_json(row["payload_json"]) for row in self.conn.execute(
            "SELECT payload_json FROM retrieval_hits WHERE retrieval_run_id=? ORDER BY rank,id",
            (run_id,),
        ).fetchall()]

    def save_skill_publication(self, item: SkillPublication) -> None:
        revision = self.get_revision(item.revision_id)
        if revision is None or revision.document_id != item.document_id:
            raise PersistenceConflict("SkillPublication revision ancestry is invalid")
        self._add_immutable(
            table="skill_publications",
            identifier=item.id,
            payload=item.model_dump_json(),
            sql=(
                "INSERT INTO skill_publications(id,revision_id,document_id,skill_name,"
                "skill_version,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)"
            ),
            parameters=(
                item.id, item.revision_id, item.document_id, item.skill_name,
                item.skill_version, item.status, item.model_dump_json(), item.created_at,
            ),
        )

    def get_skill_publication(self, revision_id: str) -> SkillPublication | None:
        row = self.conn.execute(
            "SELECT payload_json FROM skill_publications WHERE revision_id=? "
            "ORDER BY created_at DESC,id DESC LIMIT 1",
            (revision_id,),
        ).fetchone()
        return self._one(row, SkillPublication)

    def list_skill_publications(
        self, *, skill_name: str | None = None
    ) -> list[SkillPublication]:
        rows = self.conn.execute(
            "SELECT payload_json FROM skill_publications "
            "WHERE (? IS NULL OR skill_name=?) ORDER BY created_at,id",
            (skill_name, skill_name),
        ).fetchall()
        return [SkillPublication.model_validate_json(row["payload_json"]) for row in rows]

    @staticmethod
    def _source_owner_within_knowledge_base(
        source: OwnerScope, knowledge_base: OwnerScope
    ) -> bool:
        if source == knowledge_base:
            return True
        return (
            knowledge_base.scope == "task"
            and source.scope == "solver"
            and source.task_id == knowledge_base.task_id
        )


__all__ = ["SqliteRetrievalRepository"]
