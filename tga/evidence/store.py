"""SQLite evidence store."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tga.contracts import ArtifactRecord, Finding, Intent, IntentStatus, TGATask


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class EvidenceStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        schema_path = Path(__file__).with_name("schema.sql")
        self.conn.executescript(schema_path.read_text(encoding="utf-8"))
        self.conn.commit()

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

    def task_snapshot(self, task_id: str) -> dict[str, Any]:
        task = self.conn.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        return {
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

    def _json_rows(self, sql: str, task_id: str) -> list[dict[str, Any]]:
        return [
            json.loads(row["payload_json"])
            for row in self.conn.execute(sql, (task_id,)).fetchall()
        ]

