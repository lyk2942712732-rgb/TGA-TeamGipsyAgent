"""Domain repositories and the authoritative runtime read model."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tga.contracts import (
    ActionResult,
    ActionSpec,
    AgentEvent,
    ArtifactIndex,
    ArtifactRecord,
    ChallengeContract,
    ContextMetric,
    Finding,
    Intent,
    IntentStatus,
    MemoryEntry,
    SessionRecord,
    SolverRecord,
    StrategyCard,
    TGATask,
)
from tga.evidence.database import utc_now
from tga.infrastructure.persistence.errors import ArtifactImmutableError, PersistenceConflict
class TaskRepository:
    def get_task(self, task_id: str) -> TGATask | None:
        row = self.conn.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        return TGATask.model_validate_json(row["payload_json"]) if row else None

    def create_task(self, task: TGATask) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO tasks(id, payload_json, created_at) VALUES (?, ?, ?)",
            (task.id, task.model_dump_json(), utc_now()),
        )
        self._commit()

    def update_task(self, task: TGATask) -> None:
        cursor = self.conn.execute(
            "UPDATE tasks SET payload_json=? WHERE id=?",
            (task.model_dump_json(), task.id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"task not found: {task.id}")
        self._commit()

    def add_intent(self, intent: Intent) -> None:
        now = utc_now()
        self.conn.execute(
            "INSERT OR REPLACE INTO intents(id, task_id, payload_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (intent.id, intent.task_id, intent.model_dump_json(), intent.status, now, now),
        )
        self._commit()

    def update_intent_status(self, intent_id: str, status: IntentStatus) -> None:
        self.conn.execute(
            "UPDATE intents SET status=?, updated_at=? WHERE id=?",
            (status, utc_now(), intent_id),
        )
        self._commit()

    def add_event(self, task_id: str, type: str, payload: dict, intent_id: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events(task_id, intent_id, type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, intent_id, type, json.dumps(payload, ensure_ascii=False), utc_now()),
        )
        self._commit()

    def add_candidate_finding(self, finding: Finding) -> None:
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
        finding = Finding.model_validate_json(row["payload_json"])
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

    def list_findings(self, task_id: str) -> list[Finding]:
        rows = self.conn.execute(
            "SELECT payload_json FROM findings WHERE task_id=? ORDER BY created_at", (task_id,)
        ).fetchall()
        return [Finding.model_validate_json(row["payload_json"]) for row in rows]

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

    def add_solver(self, solver: SolverRecord) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO solvers(id,task_id,role,status,model_name,parent_solver_id,started_at,finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                solver.id, solver.task_id, solver.role, solver.status, solver.model_name,
                solver.parent_solver_id, solver.started_at, solver.finished_at,
            ),
        )
        self._commit()

    def update_solver(self, solver_id: str, **changes: Any) -> SolverRecord:
        allowed = {"status", "model_name", "parent_solver_id", "started_at", "finished_at"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if values:
            assignments = ", ".join(f"{key}=?" for key in values)
            cursor = self.conn.execute(f"UPDATE solvers SET {assignments} WHERE id=?", (*values.values(), solver_id))
            if cursor.rowcount == 0:
                raise KeyError(f"solver not found: {solver_id}")
            self._commit()
        row = self.conn.execute("SELECT * FROM solvers WHERE id=?", (solver_id,)).fetchone()
        if row is None:
            raise KeyError(f"solver not found: {solver_id}")
        return SolverRecord.model_validate(dict(row))

    def list_solvers(self, task_id: str) -> list[SolverRecord]:
        rows = self.conn.execute(
            "SELECT * FROM solvers WHERE task_id=? "
            "ORDER BY CASE WHEN role='main' THEN 0 ELSE 1 END, started_at, id",
            (task_id,),
        ).fetchall()
        return [SolverRecord.model_validate(dict(row)) for row in rows]

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


class MemoryRepository:
    def add_memory(self, entry: MemoryEntry) -> None:
        self.conn.execute(
            "INSERT INTO memory_entries(id,task_id,kind,content,artifact_ids_json,source,supersedes_id,created_at,updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.id, entry.task_id, entry.kind, entry.content, json.dumps(entry.artifact_ids),
                entry.source, entry.supersedes_id, entry.created_at, entry.updated_at,
            ),
        )
        self._commit()

    def list_memory(self, task_id: str, *, include_superseded: bool = False) -> list[MemoryEntry]:
        sql = "SELECT * FROM memory_entries WHERE task_id=?"
        if not include_superseded:
            sql += " AND supersedes_id IS NULL"
        sql += " ORDER BY created_at, id"
        return [self._memory_row(row) for row in self.conn.execute(sql, (task_id,)).fetchall()]

    def supersede_memory(self, memory_id: str, replacement_id: str) -> None:
        self.conn.execute(
            "UPDATE memory_entries SET supersedes_id=?, updated_at=? WHERE id=?",
            (replacement_id, utc_now(), memory_id),
        )
        self._commit()


class ActionRepository:
    def add_action(
        self,
        action: ActionSpec,
        *,
        status: str = "proposed",
        approval_expires_at: str | None = None,
    ) -> None:
        now = utc_now()
        self.conn.execute(
            "INSERT INTO actions(id,task_id,solver_id,intent_id,local_plan_step_id,execution_policy_snapshot_id,solver_tool_policy_snapshot_id,governed_action_id,kind,capability,target,arguments_json,rationale,risk,strategy_card_id,strategy_step_id,expected_outcome,retry_reason,alternative_analysis,effect_json,approval_expires_at,input_id,target_ref,actual_target,authorization_json,provenance_json,status,created_at,updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action.id, action.task_id, action.solver_id, action.intent_id,
                action.local_plan_step_id, action.execution_policy_snapshot_id,
                action.solver_tool_policy_snapshot_id, action.governed_action_id, action.kind,
                action.capability, action.target, json.dumps(action.arguments), action.rationale,
                action.risk, action.strategy_card_id, action.strategy_step_id,
                action.expected_outcome, action.retry_reason, action.alternative_analysis,
                action.effect.model_dump_json(), approval_expires_at, action.input_id, action.target_ref,
                action.actual_target or action.target, json.dumps(action.authorization, ensure_ascii=False),
                json.dumps(action.provenance, ensure_ascii=False), status, now, now,
            ),
        )
        self._commit()

    def update_action_status(self, action_id: str, status: str, *, expected_status: str | None = None) -> None:
        if expected_status is None:
            cursor = self.conn.execute("UPDATE actions SET status=?, updated_at=? WHERE id=?", (status, utc_now(), action_id))
        else:
            cursor = self.conn.execute(
                "UPDATE actions SET status=?, updated_at=? WHERE id=? AND status=?",
                (status, utc_now(), action_id, expected_status),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"action transition rejected: {action_id}")
        self._commit()

    def get_action(self, task_id: str, action_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM actions WHERE task_id=? AND id=?", (task_id, action_id)).fetchone()
        if row is None:
            return None
        return {
            **dict(row),
            "arguments": json.loads(row["arguments_json"]),
            "effect": json.loads(row["effect_json"] or "{}"),
            "authorization": json.loads(row["authorization_json"] or "{}"),
            "provenance": json.loads(row["provenance_json"] or "{}"),
            "result": self.get_action_result(row["id"]),
        }

    def get_action_spec(self, task_id: str, action_id: str) -> ActionSpec | None:
        item = self.get_action(task_id, action_id)
        if item is None:
            return None
        return ActionSpec.model_validate({
            key: item[key]
            for key in ActionSpec.model_fields
            if key in item
        })

    def list_actions(self, task_id: str) -> list[dict[str, Any]]:
        return [
            {
                **dict(row),
                "arguments": json.loads(row["arguments_json"]),
                "effect": json.loads(row["effect_json"] or "{}"),
                "authorization": json.loads(row["authorization_json"] or "{}"),
                "provenance": json.loads(row["provenance_json"] or "{}"),
                "result": self.get_action_result(row["id"]),
            }
            for row in self.conn.execute("SELECT * FROM actions WHERE task_id=? ORDER BY created_at, id", (task_id,)).fetchall()
        ]

    def add_action_result(self, result: ActionResult) -> None:
        values = {
            "action_id": result.action_id,
            "summary": result.summary,
            "artifact_ids": result.artifact_ids,
            "facts": result.facts,
            "leads": result.leads,
            "candidate_flags": result.candidate_flags,
            "candidate_findings": [
                finding.model_dump(mode="json") for finding in result.candidate_findings
            ],
            "error": result.error.model_dump(mode="json") if result.error else None,
        }
        action_row = self.conn.execute(
            "SELECT task_id FROM actions WHERE id=?", (result.action_id,)
        ).fetchone()
        task_row = self.conn.execute(
            "SELECT payload_json FROM tasks WHERE id=?",
            (action_row["task_id"],),
        ).fetchone() if action_row else None
        schema_version = int(json.loads(task_row["payload_json"]).get("schema_version") or 0) if task_row else 0
        existing = self.get_action_result(result.action_id)
        if schema_version >= 6 and existing is not None:
            persisted = {key: existing[key] for key in values}
            if persisted == values:
                return
            raise PersistenceConflict(
                f"schema-v6 ActionResult is immutable: {result.action_id}"
            )
        verb = "INSERT" if schema_version >= 6 else "INSERT OR REPLACE"
        self.conn.execute(
            f"{verb} INTO action_results(action_id,summary,artifact_ids_json,facts_json,leads_json,flags_json,findings_json,error_json,created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.action_id, result.summary, json.dumps(result.artifact_ids), json.dumps(result.facts),
                json.dumps(result.leads), json.dumps(result.candidate_flags),
                json.dumps(values["candidate_findings"]),
                result.error.model_dump_json() if result.error else None, utc_now(),
            ),
        )
        self._commit()

    def get_action_result(self, action_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM action_results WHERE action_id=?", (action_id,)).fetchone()
        if row is None:
            return None
        return {
            "action_id": row["action_id"], "summary": row["summary"],
            "artifact_ids": json.loads(row["artifact_ids_json"]), "facts": json.loads(row["facts_json"]),
            "leads": json.loads(row["leads_json"]), "candidate_flags": json.loads(row["flags_json"]),
            "candidate_findings": json.loads(row["findings_json"]),
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "created_at": row["created_at"],
        }


class StrategyRepository:
    def upsert_strategy_card(self, card: StrategyCard) -> StrategyCard:
        self.conn.execute(
            "INSERT INTO strategy_cards(id,task_id,payload_json,status,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json,status=excluded.status,updated_at=excluded.updated_at",
            (card.id, card.task_id, card.model_dump_json(), card.status, card.created_at, card.updated_at),
        )
        self._commit()
        return card

    def get_strategy_card(self, card_id: str) -> StrategyCard | None:
        row = self.conn.execute("SELECT payload_json FROM strategy_cards WHERE id=?", (card_id,)).fetchone()
        return StrategyCard.model_validate_json(row["payload_json"]) if row else None

    def list_strategy_cards(self, task_id: str) -> list[StrategyCard]:
        rows = self.conn.execute(
            "SELECT payload_json FROM strategy_cards WHERE task_id=? ORDER BY created_at,id", (task_id,)
        ).fetchall()
        return [StrategyCard.model_validate_json(row["payload_json"]) for row in rows]


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


class RuntimeReadModel:
    def task_snapshot(self, task_id: str) -> dict[str, Any]:
        task = self.conn.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        task_payload = TGATask.model_validate_json(task["payload_json"]).model_dump(mode="json") if task else None
        snapshot = {
            "task": task_payload,
            "intents": self._json_rows("SELECT payload_json FROM intents WHERE task_id=? ORDER BY created_at", task_id),
            "artifacts": self._json_rows("SELECT payload_json FROM artifacts WHERE task_id=? ORDER BY created_at", task_id),
            "findings": self._json_rows("SELECT payload_json FROM findings WHERE task_id=? ORDER BY created_at", task_id),
            "flags": [
                dict(row)
                for row in self.conn.execute(
                    "SELECT value, evidence_artifact_id, created_at FROM flags WHERE task_id=? ORDER BY created_at",
                    (task_id,),
                ).fetchall()
            ],
            "events": [
                {
                    "id": row["id"],
                    "intent_id": row["intent_id"],
                    "type": row["type"],
                    "payload": json.loads(row["payload_json"]),
                    "created_at": row["created_at"],
                }
                for row in self.conn.execute(
                    "SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,)
                ).fetchall()
            ],
        }
        session = self.get_session(task_id)
        if session is not None:
            snapshot.update(
                {
                    "session": session.model_dump(mode="json"),
                    "solvers": [solver.model_dump(mode="json") for solver in self.list_solvers(task_id)],
                    "challenge": self.get_challenge(task_id).model_dump(mode="json") if self.get_challenge(task_id) else None,
                    "runtime": {
                        "memory": [item.model_dump(mode="json") for item in self.list_memory(task_id)],
                        "strategy_cards": [item.model_dump(mode="json") for item in self.list_strategy_cards(task_id)],
                    },
                    "artifact_indexes": [item.model_dump(mode="json") for item in self.list_artifact_indexes(task_id)],
                    "context_metrics": [item.model_dump(mode="json") for item in self.list_context_metrics(task_id)],
                    "actions": self.list_actions(task_id),
                    "agent_events": [item.model_dump(mode="json") for item in self.list_agent_events(task_id, limit=None)],
                }
            )
        return snapshot

    def get_session_snapshot(self, task_id: str) -> dict[str, Any]:
        """Public v2 read repository used by API/UI adapters."""
        return self.task_snapshot(task_id)

    def list_events(self, task_id: str, *, after_seq: int = 0, limit: int = 200) -> list[AgentEvent]:
        """Public cursor for the runtime's authoritative event stream."""
        return self.list_agent_events(task_id, after_seq=after_seq, limit=limit)

    def _json_rows(self, sql: str, task_id: str) -> list[dict[str, Any]]:
        return [
            json.loads(row["payload_json"])
            for row in self.conn.execute(sql, (task_id,)).fetchall()
        ]

    @staticmethod
    def _memory_row(row: sqlite3.Row) -> MemoryEntry:
        data = dict(row)
        data["artifact_ids"] = json.loads(data.pop("artifact_ids_json"))
        return MemoryEntry.model_validate(data)


def _compact_event_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact_event_payload(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_compact_event_payload(item) for item in value]
    return value
