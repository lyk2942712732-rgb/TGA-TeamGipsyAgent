"""Domain repositories and the authoritative runtime read model."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tga.contracts import (
    AgentEvent,
    ArtifactIndex,
    ArtifactRecord,
    CandidateFindingRecord,
    ChallengeContract,
    ContextMetric,
    SessionRecord,
    TGATask,
)
from tga.evidence.database import utc_now
from tga.infrastructure.persistence.errors import ArtifactImmutableError, PersistenceConflict
class TaskRepository:
    def get_task(self, task_id: str) -> TGATask | None:
        row = self.conn.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        return TGATask.model_validate_json(row["payload_json"]) if row else None

    def create_task(self, task: TGATask) -> None:
        if task.schema_version != 6:
            raise ValueError("runtime persistence accepts only task schema 6")
        self.conn.execute(
            "INSERT OR REPLACE INTO tasks(id, payload_json, created_at) VALUES (?, ?, ?)",
            (task.id, task.model_dump_json(), utc_now()),
        )
        self._commit()

    def update_task(self, task: TGATask) -> None:
        if task.schema_version != 6:
            raise ValueError("runtime persistence accepts only task schema 6")
        cursor = self.conn.execute(
            "UPDATE tasks SET payload_json=? WHERE id=?",
            (task.model_dump_json(), task.id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"task not found: {task.id}")
        self._commit()

    def add_candidate_finding(self, finding: CandidateFindingRecord) -> None:
        payload = finding.model_copy(update={"status": "candidate"}).model_dump_json()
        now = utc_now()
        self.conn.execute(
            "INSERT OR REPLACE INTO findings(id, task_id, payload_json, status, evidence_artifact_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (finding.id, finding.task_id, payload, "candidate", finding.evidence_artifact_id, now, now),
        )
        self._commit()

    def confirm_finding(self, finding_id: str, evidence_artifact_id: str) -> None:
        row = self.conn.execute("SELECT payload_json FROM findings WHERE id=?", (finding_id,)).fetchone()
        if row is None:
            raise KeyError(f"finding not found: {finding_id}")
        finding = CandidateFindingRecord.model_validate_json(row["payload_json"])
        confirmed = finding.model_copy(
            update={"status": "confirmed", "evidence_artifact_id": evidence_artifact_id}
        )
        self.conn.execute(
            "UPDATE findings SET payload_json=?, status=?, evidence_artifact_id=?, updated_at=? WHERE id=?",
            (confirmed.model_dump_json(), "confirmed", evidence_artifact_id, utc_now(), finding_id),
        )
        self._commit()

    def add_flag(self, task_id: str, value: str, evidence_artifact_id: str) -> None:
        existing = self.conn.execute(
            "SELECT 1 FROM flags WHERE task_id=? AND value=?",
            (task_id, value),
        ).fetchone()
        if existing is not None:
            return
        self.conn.execute(
            "INSERT INTO flags(task_id, value, evidence_artifact_id, created_at) VALUES (?, ?, ?, ?)",
            (task_id, value, evidence_artifact_id, utc_now()),
        )
        self._commit()

    def list_flags(self, task_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT value,evidence_artifact_id,created_at FROM flags WHERE task_id=? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        ]

    def list_findings(self, task_id: str) -> list[CandidateFindingRecord]:
        rows = self.conn.execute(
            "SELECT payload_json FROM findings WHERE task_id=? ORDER BY created_at", (task_id,)
        ).fetchall()
        return [CandidateFindingRecord.model_validate_json(row["payload_json"]) for row in rows]

    # v2 runtime repository -------------------------------------------------
    # All v2 writes live here so manager, observer and API readers do not
    # reach into SQLite independently.


class SessionRepository:
    def acquire_runtime_lease(self, task_id: str, owner_id: str, *, ttl_seconds: float) -> bool:
        now = time.time()
        cursor = self.conn.execute(
            "INSERT INTO runtime_leases(task_id,owner_id,expires_at,updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET owner_id=excluded.owner_id,expires_at=excluded.expires_at,updated_at=excluded.updated_at "
            "WHERE runtime_leases.expires_at < ? OR runtime_leases.owner_id = excluded.owner_id",
            (task_id, owner_id, now + ttl_seconds, utc_now(), now),
        )
        self._commit()
        return cursor.rowcount == 1

    def renew_runtime_lease(self, task_id: str, owner_id: str, *, ttl_seconds: float) -> bool:
        cursor = self.conn.execute(
            "UPDATE runtime_leases SET expires_at=?,updated_at=? WHERE task_id=? AND owner_id=?",
            (time.time() + ttl_seconds, utc_now(), task_id, owner_id),
        )
        self._commit()
        return cursor.rowcount == 1

    def release_runtime_lease(self, task_id: str, owner_id: str) -> None:
        self.conn.execute(
            "DELETE FROM runtime_leases WHERE task_id=? AND owner_id=?",
            (task_id, owner_id),
        )
        self._commit()

    def create_session(self, session: SessionRecord) -> SessionRecord:
        self.conn.execute(
            "INSERT OR IGNORE INTO sessions(task_id,schema_version,status,active_solver_id,turn_count,max_turns,started_at,finished_at,stop_reason,workspace_path,mcp_catalog_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.task_id, session.schema_version, session.status, session.active_solver_id, session.turn_count,
                session.max_turns, session.started_at, session.finished_at, session.stop_reason,
                session.workspace_path, session.mcp_catalog_version,
            ),
        )
        self._commit()
        return self.get_session(session.task_id) or session

    def get_session(self, task_id: str) -> SessionRecord | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE task_id=?", (task_id,)).fetchone()
        return SessionRecord.model_validate(dict(row)) if row else None

    def update_session(self, task_id: str, **changes: Any) -> SessionRecord:
        allowed = {"schema_version", "status", "active_solver_id", "turn_count", "max_turns", "started_at", "finished_at", "stop_reason", "workspace_path", "mcp_catalog_version"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            session = self.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            return session
        assignments = ", ".join(f"{key}=?" for key in values)
        cursor = self.conn.execute(
            f"UPDATE sessions SET {assignments} WHERE task_id=?", (*values.values(), task_id)
        )
        if cursor.rowcount == 0:
            raise KeyError(f"session not found: {task_id}")
        self._commit()
        return self.get_session(task_id)  # type: ignore[return-value]

    def reserve_turn(self, task_id: str) -> SessionRecord | None:
        """Atomically consume one task turn before a provider request."""
        cursor = self.conn.execute(
            "UPDATE sessions SET turn_count=turn_count+1 "
            "WHERE task_id=? AND status='running' AND turn_count<max_turns",
            (task_id,),
        )
        self._commit()
        if cursor.rowcount != 1:
            return None
        return self.get_session(task_id)

    def upsert_challenge(self, challenge: ChallengeContract) -> ChallengeContract:
        self.conn.execute(
            "INSERT INTO challenge_contracts(task_id,payload_json,updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
            (challenge.task_id, challenge.model_dump_json(), utc_now()),
        )
        self._commit()
        return challenge

    def get_challenge(self, task_id: str) -> ChallengeContract | None:
        row = self.conn.execute(
            "SELECT payload_json FROM challenge_contracts WHERE task_id=?", (task_id,)
        ).fetchone()
        return ChallengeContract.model_validate_json(row["payload_json"]) if row else None


class ArtifactRepository:
    def add_artifact(self, artifact: ArtifactRecord) -> None:
        task_row = self.conn.execute(
            "SELECT payload_json FROM tasks WHERE id=?", (artifact.task_id,)
        ).fetchone()
        schema_version = (
            int(json.loads(task_row["payload_json"]).get("schema_version") or 0)
            if task_row else 0
        )
        payload = artifact.model_dump_json()
        if schema_version == 6:
            existing = self.conn.execute(
                "SELECT task_id,payload_json,schema_version FROM artifacts WHERE id=?",
                (artifact.id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["task_id"] == artifact.task_id
                    and existing["payload_json"] == payload
                    and int(existing["schema_version"]) == 6
                ):
                    return
                raise ArtifactImmutableError(
                    f"artifact id is immutable: {artifact.id}"
                )
            self.conn.execute(
                "INSERT INTO artifacts(id,task_id,intent_id,payload_json,created_at,schema_version) "
                "VALUES (?,?,?,?,?,6)",
                (
                    artifact.id, artifact.task_id, artifact.intent_id,
                    payload, artifact.created_at,
                ),
            )
        else:
            self.conn.execute(
                "INSERT OR REPLACE INTO artifacts(id, task_id, intent_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    artifact.id, artifact.task_id, artifact.intent_id,
                    payload, artifact.created_at,
                ),
            )
        self._commit()

    def upsert_artifact_index(self, index: ArtifactIndex) -> ArtifactIndex:
        self.conn.execute(
            "INSERT INTO artifact_indexes(artifact_id,task_id,payload_json,created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(artifact_id) DO UPDATE SET payload_json=excluded.payload_json",
            (index.artifact_id, index.task_id, index.model_dump_json(), index.created_at),
        )
        self._commit()
        return index

    def get_artifact_index(self, artifact_id: str) -> ArtifactIndex | None:
        row = self.conn.execute("SELECT payload_json FROM artifact_indexes WHERE artifact_id=?", (artifact_id,)).fetchone()
        return ArtifactIndex.model_validate_json(row["payload_json"]) if row else None

    def list_artifact_indexes(self, task_id: str) -> list[ArtifactIndex]:
        rows = self.conn.execute(
            "SELECT payload_json FROM artifact_indexes WHERE task_id=? ORDER BY created_at,artifact_id", (task_id,)
        ).fetchall()
        return [ArtifactIndex.model_validate_json(row["payload_json"]) for row in rows]

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        row = self.conn.execute("SELECT payload_json FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return ArtifactRecord.model_validate_json(row["payload_json"]) if row else None

    def list_artifacts(self, task_id: str) -> list[ArtifactRecord]:
        rows = self.conn.execute(
            "SELECT payload_json FROM artifacts WHERE task_id=? ORDER BY created_at,id", (task_id,)
        ).fetchall()
        return [ArtifactRecord.model_validate_json(row["payload_json"]) for row in rows]


class ContextMetricRepository:
    def add_context_metric(self, metric: ContextMetric) -> None:
        self.conn.execute(
            "INSERT INTO context_metrics(task_id,solver_id,turn,payload_json,created_at) VALUES (?, ?, ?, ?, ?)",
            (metric.task_id, metric.solver_id, metric.turn, metric.model_dump_json(), metric.created_at),
        )
        self._commit()

    def list_context_metrics(self, task_id: str) -> list[ContextMetric]:
        rows = self.conn.execute(
            "SELECT payload_json FROM context_metrics WHERE task_id=? ORDER BY id", (task_id,)
        ).fetchall()
        return [ContextMetric.model_validate_json(row["payload_json"]) for row in rows]


class EventRepository:
    def append_agent_event(
        self, task_id: str, type: str, payload: dict[str, Any], *,
        solver_id: str | None = None, intent_id: str | None = None,
    ) -> AgentEvent:
        # Event payloads are an evolvable audit envelope.  Persisting optional
        # fields as JSON null made older clients reject an entire snapshot when
        # a control event had no action_id.  Omit absent values at the write
        # boundary instead; concrete false/zero values remain intact.
        from tga.domain.events import normalize_event_payload

        payload = normalize_event_payload(type, _compact_event_payload(payload))
        event_id = f"evt_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}_{uuid4().hex[:8]}"
        now = utc_now()
        seq = int(self.conn.execute(
            "INSERT INTO agent_event_sequences(task_id, next_seq) VALUES (?, 2) "
            "ON CONFLICT(task_id) DO UPDATE SET next_seq=agent_event_sequences.next_seq + 1 "
            "RETURNING next_seq - 1",
            (task_id,),
        ).fetchone()[0])
        self.conn.execute(
            "INSERT INTO agent_events(id,schema_version,task_id,solver_id,intent_id,seq,type,payload_json,created_at) VALUES (?, 6, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, task_id, solver_id, intent_id, seq, type, json.dumps(payload, ensure_ascii=False), now),
        )
        self._commit()
        event = AgentEvent(
            schema_version=6, id=event_id, task_id=task_id,
            solver_id=solver_id, intent_id=intent_id, seq=seq, type=type,
            payload=payload, created_at=now,
        )
        from tga.infrastructure.events import runtime_event_bus

        runtime_event_bus.publish(event)
        return event

    def list_agent_events(self, task_id: str, *, after_seq: int = 0, limit: int | None = 200) -> list[AgentEvent]:
        if limit is None:
            rows = self.conn.execute(
                "SELECT * FROM agent_events WHERE task_id=? AND seq>? ORDER BY seq", (task_id, after_seq)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM agent_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                (task_id, after_seq, max(1, min(limit, 1000))),
            ).fetchall()
        return [
            AgentEvent(
                schema_version=row["schema_version"], id=row["id"],
                task_id=row["task_id"], solver_id=row["solver_id"],
                intent_id=row["intent_id"], seq=row["seq"], type=row["type"],
                payload=json.loads(row["payload_json"]), created_at=row["created_at"],
            )
            for row in rows
        ]

    def latest_agent_event_seq(self, task_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS seq FROM agent_events WHERE task_id=?", (task_id,)
        ).fetchone()
        return int(row["seq"])


def _compact_event_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact_event_payload(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_compact_event_payload(item) for item in value]
    return value
