"""SQLite evidence store."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tga.contracts import (
    ActionResult,
    ActionSpec,
    AgentEvent,
    ArtifactRecord,
    Finding,
    Hypothesis,
    Intent,
    IntentStatus,
    MemoryEntry,
    SessionRecord,
    SolverRecord,
    TGATask,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class EvidenceStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.row_factory = sqlite3.Row
        schema_path = Path(__file__).with_name("schema.sql")
        self.conn.executescript(schema_path.read_text(encoding="utf-8"))
        self._migrate_v2_schema()
        self.conn.commit()

    def _migrate_v2_schema(self) -> None:
        """Apply additive v2 columns to databases created by older builds."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "schema_version" not in columns:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 2")

    def create_task(self, task: TGATask) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO tasks(id, payload_json, created_at) VALUES (?, ?, ?)",
            (task.id, task.model_dump_json(), utc_now()),
        )
        self.conn.commit()

    def add_intent(self, intent: Intent) -> None:
        now = utc_now()
        self.conn.execute(
            "INSERT OR REPLACE INTO intents(id, task_id, payload_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (intent.id, intent.task_id, intent.model_dump_json(), intent.status, now, now),
        )
        self.conn.commit()

    def update_intent_status(self, intent_id: str, status: IntentStatus) -> None:
        self.conn.execute(
            "UPDATE intents SET status=?, updated_at=? WHERE id=?",
            (status, utc_now(), intent_id),
        )
        self.conn.commit()

    def add_artifact(self, artifact: ArtifactRecord) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO artifacts(id, task_id, intent_id, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (artifact.id, artifact.task_id, artifact.intent_id, artifact.model_dump_json(), artifact.created_at),
        )
        self.conn.commit()

    def add_event(self, task_id: str, type: str, payload: dict, intent_id: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events(task_id, intent_id, type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, intent_id, type, json.dumps(payload, ensure_ascii=False), utc_now()),
        )
        self.conn.commit()

    def add_candidate_finding(self, finding: Finding) -> None:
        payload = finding.model_copy(update={"status": "candidate"}).model_dump_json()
        now = utc_now()
        self.conn.execute(
            "INSERT OR REPLACE INTO findings(id, task_id, payload_json, status, evidence_artifact_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (finding.id, finding.task_id, payload, "candidate", finding.evidence_artifact_id, now, now),
        )
        self.conn.commit()

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
        self.conn.commit()

    def add_flag(self, task_id: str, value: str, evidence_artifact_id: str) -> None:
        self.conn.execute(
            "INSERT INTO flags(task_id, value, evidence_artifact_id, created_at) VALUES (?, ?, ?, ?)",
            (task_id, value, evidence_artifact_id, utc_now()),
        )
        self.conn.commit()

    # v2 runtime repository -------------------------------------------------
    # All v2 writes live here so manager, observer and API readers do not
    # reach into SQLite independently.

    def create_session(self, session: SessionRecord) -> SessionRecord:
        self.conn.execute(
            "INSERT OR IGNORE INTO sessions(task_id,schema_version,status,active_solver_id,turn_count,max_turns,started_at,finished_at,stop_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.task_id, session.schema_version, session.status, session.active_solver_id, session.turn_count,
                session.max_turns, session.started_at, session.finished_at, session.stop_reason,
            ),
        )
        self.conn.commit()
        return self.get_session(session.task_id) or session

    def get_session(self, task_id: str) -> SessionRecord | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE task_id=?", (task_id,)).fetchone()
        return SessionRecord.model_validate(dict(row)) if row else None

    def update_session(self, task_id: str, **changes: Any) -> SessionRecord:
        allowed = {"schema_version", "status", "active_solver_id", "turn_count", "max_turns", "started_at", "finished_at", "stop_reason"}
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
        self.conn.commit()
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
        self.conn.commit()

    def update_solver(self, solver_id: str, **changes: Any) -> SolverRecord:
        allowed = {"status", "model_name", "parent_solver_id", "started_at", "finished_at"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if values:
            assignments = ", ".join(f"{key}=?" for key in values)
            cursor = self.conn.execute(f"UPDATE solvers SET {assignments} WHERE id=?", (*values.values(), solver_id))
            if cursor.rowcount == 0:
                raise KeyError(f"solver not found: {solver_id}")
            self.conn.commit()
        row = self.conn.execute("SELECT * FROM solvers WHERE id=?", (solver_id,)).fetchone()
        if row is None:
            raise KeyError(f"solver not found: {solver_id}")
        return SolverRecord.model_validate(dict(row))

    def list_solvers(self, task_id: str) -> list[SolverRecord]:
        rows = self.conn.execute("SELECT * FROM solvers WHERE task_id=? ORDER BY started_at, id", (task_id,)).fetchall()
        return [SolverRecord.model_validate(dict(row)) for row in rows]

    def add_hypothesis(self, hypothesis: Hypothesis) -> None:
        self.conn.execute(
            "INSERT INTO hypotheses(id,task_id,statement,attack_class,entry_point,rationale,next_test,status,confidence,attempt_count,evidence_json,last_result,owner_solver_id,created_at,updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                hypothesis.id, hypothesis.task_id, hypothesis.statement, hypothesis.attack_class,
                hypothesis.entry_point, hypothesis.rationale, hypothesis.next_test, hypothesis.status,
                hypothesis.confidence, hypothesis.attempt_count, json.dumps(hypothesis.evidence_artifact_ids),
                hypothesis.last_result, hypothesis.owner_solver_id, hypothesis.created_at, hypothesis.updated_at,
            ),
        )
        self.conn.commit()

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        row = self.conn.execute("SELECT * FROM hypotheses WHERE id=?", (hypothesis_id,)).fetchone()
        return self._hypothesis_row(row) if row else None

    def list_hypotheses(self, task_id: str, *, active_only: bool = False) -> list[Hypothesis]:
        sql = "SELECT * FROM hypotheses WHERE task_id=?"
        if active_only:
            sql += " AND status NOT IN ('rejected', 'superseded')"
        sql += " ORDER BY created_at, id"
        return [self._hypothesis_row(row) for row in self.conn.execute(sql, (task_id,)).fetchall()]

    def update_hypothesis(self, hypothesis_id: str, **changes: Any) -> Hypothesis:
        allowed = {"statement", "attack_class", "entry_point", "rationale", "next_test", "status", "confidence", "attempt_count", "evidence_artifact_ids", "last_result", "owner_solver_id", "updated_at"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if "evidence_artifact_ids" in values:
            values["evidence_json"] = json.dumps(values.pop("evidence_artifact_ids"))
        if values:
            values.setdefault("updated_at", utc_now())
            assignments = ", ".join(f"{key}=?" for key in values)
            cursor = self.conn.execute(f"UPDATE hypotheses SET {assignments} WHERE id=?", (*values.values(), hypothesis_id))
            if cursor.rowcount == 0:
                raise KeyError(f"hypothesis not found: {hypothesis_id}")
            self.conn.commit()
        result = self.get_hypothesis(hypothesis_id)
        if result is None:
            raise KeyError(f"hypothesis not found: {hypothesis_id}")
        return result

    def add_memory(self, entry: MemoryEntry) -> None:
        self.conn.execute(
            "INSERT INTO memory_entries(id,task_id,kind,content,artifact_ids_json,source,supersedes_id,created_at,updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.id, entry.task_id, entry.kind, entry.content, json.dumps(entry.artifact_ids),
                entry.source, entry.supersedes_id, entry.created_at, entry.updated_at,
            ),
        )
        self.conn.commit()

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
        self.conn.commit()

    def add_action(self, action: ActionSpec, *, status: str = "proposed") -> None:
        now = utc_now()
        self.conn.execute(
            "INSERT INTO actions(id,task_id,solver_id,hypothesis_id,kind,capability,target,arguments_json,rationale,risk,status,created_at,updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action.id, action.task_id, action.solver_id, action.hypothesis_id, action.kind,
                action.capability, action.target, json.dumps(action.arguments), action.rationale,
                action.risk, status, now, now,
            ),
        )
        self.conn.commit()

    def update_action_status(self, action_id: str, status: str) -> None:
        self.conn.execute("UPDATE actions SET status=?, updated_at=? WHERE id=?", (status, utc_now(), action_id))
        self.conn.commit()

    def list_actions(self, task_id: str) -> list[dict[str, Any]]:
        return [
            {
                **dict(row),
                "arguments": json.loads(row["arguments_json"]),
                "result": self.get_action_result(row["id"]),
            }
            for row in self.conn.execute("SELECT * FROM actions WHERE task_id=? ORDER BY created_at, id", (task_id,)).fetchall()
        ]

    def add_action_result(self, result: ActionResult) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO action_results(action_id,summary,artifact_ids_json,facts_json,leads_json,flags_json,findings_json,error_json,created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.action_id, result.summary, json.dumps(result.artifact_ids), json.dumps(result.facts),
                json.dumps(result.leads), json.dumps(result.candidate_flags),
                json.dumps([finding.model_dump(mode="json") for finding in result.candidate_findings]),
                result.error.model_dump_json() if result.error else None, utc_now(),
            ),
        )
        self.conn.commit()

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

    def append_agent_event(self, *, task_id: str, type: str, payload: dict[str, Any], solver_id: str | None = None) -> AgentEvent:
        # Event payloads are an evolvable audit envelope.  Persisting optional
        # fields as JSON null made older clients reject an entire snapshot when
        # a control event had no action_id.  Omit absent values at the write
        # boundary instead; concrete false/zero values remain intact.
        payload = _compact_event_payload(payload)
        event_id = f"evt_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        now = utc_now()
        with self.conn:
            seq = int(self.conn.execute(
                "INSERT INTO agent_event_sequences(task_id, next_seq) VALUES (?, 2) "
                "ON CONFLICT(task_id) DO UPDATE SET next_seq=agent_event_sequences.next_seq + 1 "
                "RETURNING next_seq - 1",
                (task_id,),
            ).fetchone()[0])
            self.conn.execute(
                "INSERT INTO agent_events(id,task_id,solver_id,seq,type,payload_json,created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, task_id, solver_id, seq, type, json.dumps(payload, ensure_ascii=False), now),
            )
        return AgentEvent(id=event_id, task_id=task_id, solver_id=solver_id, seq=seq, type=type, payload=payload, created_at=now)

    def list_agent_events(self, task_id: str, *, after_seq: int = 0, limit: int = 200) -> list[AgentEvent]:
        rows = self.conn.execute(
            "SELECT * FROM agent_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
            (task_id, after_seq, max(1, min(limit, 1000))),
        ).fetchall()
        return [
            AgentEvent(id=row["id"], task_id=row["task_id"], solver_id=row["solver_id"], seq=row["seq"], type=row["type"], payload=json.loads(row["payload_json"]), created_at=row["created_at"])
            for row in rows
        ]

    def latest_agent_event_seq(self, task_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS seq FROM agent_events WHERE task_id=?", (task_id,)
        ).fetchone()
        return int(row["seq"])

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        row = self.conn.execute("SELECT payload_json FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return ArtifactRecord.model_validate_json(row["payload_json"]) if row else None

    def task_snapshot(self, task_id: str) -> dict[str, Any]:
        task = self.conn.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        snapshot = {
            "task": json.loads(task["payload_json"]) if task else None,
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
                    "board": {
                        "hypotheses": [item.model_dump(mode="json") for item in self.list_hypotheses(task_id)],
                        "memory": [item.model_dump(mode="json") for item in self.list_memory(task_id)],
                    },
                    "actions": self.list_actions(task_id),
                    "agent_events": [item.model_dump(mode="json") for item in self.list_agent_events(task_id)],
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
    def _hypothesis_row(row: sqlite3.Row) -> Hypothesis:
        data = dict(row)
        data["evidence_artifact_ids"] = json.loads(data.pop("evidence_json"))
        return Hypothesis.model_validate(data)

    @staticmethod
    def _memory_row(row: sqlite3.Row) -> MemoryEntry:
        data = dict(row)
        data["artifact_ids"] = json.loads(data.pop("artifact_ids_json"))
        return MemoryEntry.model_validate(data)

    def close(self) -> None:
        self.conn.close()


def _compact_event_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact_event_payload(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_compact_event_payload(item) for item in value]
    return value
