"""Read-only schema-v5 snapshots, replay, and conservative projections."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tga.domain.evidence.artifacts import Artifact
from tga.domain.evidence.claims import EvidenceClaim
from tga.domain.evidence.findings import Finding
from tga.domain.events import AgentEvent
from tga.domain.knowledge.items import KnowledgeItem
from tga.migrations.converters import (
    artifact_record_to_artifact,
    legacy_finding_to_evidence,
    memory_entry_to_knowledge,
    memory_entry_to_task_hint,
    strategy_card_to_plans,
)
from tga.domain.planning.global_plan import GlobalPlan
from tga.domain.planning.local_plan import LocalPlan
from tga.migrations.legacy_models import LegacyV5Task, MemoryEntry, StrategyCard
from tga.migrations.evidence_models import LegacyArtifactRecord, LegacyFinding
from tga.domain.task.hints import TaskHint


class LegacyV5TaskReader:
    """Open one legacy database using SQLite's enforced read-only mode."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        self.conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only=ON")
        self._validate_v5()

    def _validate_v5(self) -> None:
        rows = self.conn.execute("SELECT payload_json FROM tasks").fetchall()
        if not rows:
            raise ValueError("legacy database has no task")
        versions = {
            int(json.loads(row["payload_json"]).get("schema_version") or 0) for row in rows
        }
        if versions != {5}:
            raise ValueError(f"LegacyV5TaskReader requires schema 5, found {sorted(versions)}")

    def snapshot(self, task_id: str) -> dict[str, Any]:
        task_row = self.conn.execute(
            "SELECT payload_json FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if task_row is None:
            raise KeyError(f"task not found: {task_id}")
        task_payload = json.loads(task_row["payload_json"])
        # Validate through the legacy model: the runtime TGATask is strictly
        # schema 6 and must not be relaxed to read historical rows.
        LegacyV5Task.model_validate(task_payload)
        result: dict[str, Any] = {"task": task_payload}
        for key, table, order in (
            ("intents", "intents", "created_at,id"),
            ("artifacts", "artifacts", "created_at,id"),
            ("findings", "findings", "created_at,id"),
            ("strategy_cards", "strategy_cards", "created_at,id"),
        ):
            if self._has_table(table):
                result[key] = self._payload_rows(table, task_id, order)
            else:
                result[key] = []
        result["memory"] = self._memory_rows(task_id) if self._has_table("memory_entries") else []
        session = None
        if self._has_table("sessions"):
            row = self.conn.execute("SELECT * FROM sessions WHERE task_id=?", (task_id,)).fetchone()
            session = dict(row) if row else None
        result["session"] = session
        result["agent_events"] = [
            event.model_dump(mode="json") for event in self.replay(task_id, limit=1000)
        ]
        return result

    def replay(
        self, task_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[AgentEvent]:
        if not self._has_table("agent_events"):
            return []
        rows = self.conn.execute(
            "SELECT * FROM agent_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
            (task_id, after_seq, max(1, min(limit, 1000))),
        ).fetchall()
        return [
            AgentEvent(
                schema_version=row["schema_version"], id=row["id"], task_id=row["task_id"],
                solver_id=row["solver_id"], seq=row["seq"], type=row["type"],
                payload=json.loads(row["payload_json"]), created_at=row["created_at"],
            )
            for row in rows
        ]

    def latest_event_seq(self, task_id: str) -> int:
        if not self._has_table("agent_events"):
            return 0
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq),0) FROM agent_events WHERE task_id=?",
            (task_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    def _payload_rows(self, table: str, task_id: str, order: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            f"SELECT payload_json FROM {table} WHERE task_id=? ORDER BY {order}", (task_id,)
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _memory_rows(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM memory_entries WHERE task_id=? ORDER BY created_at,id", (task_id,)
        ).fetchall()
        return [
            {
                **dict(row),
                "artifact_ids": json.loads(row["artifact_ids_json"]),
            }
            for row in rows
        ]

    def _has_table(self, name: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def close(self) -> None:
        self.conn.close()


class LegacyStrategyProjection:
    def project(
        self, card: StrategyCard, *, solver_id: str, intent_id: str
    ) -> tuple[GlobalPlan, LocalPlan]:
        return strategy_card_to_plans(card, solver_id=solver_id, intent_id=intent_id)


class LegacyMemoryProjection:
    def project_hint(self, entry: MemoryEntry) -> TaskHint:
        return memory_entry_to_task_hint(entry)

    def project_knowledge(
        self, entry: MemoryEntry, *, created_by_solver_id: str
    ) -> KnowledgeItem:
        if entry.kind != "evidence":
            return memory_entry_to_knowledge(
                entry, created_by_solver_id=created_by_solver_id
            )
        return KnowledgeItem(
            id=entry.id,
            task_id=entry.task_id,
            scope="task",
            status="candidate",
            kind="fact",
            content=entry.content,
            evidence_claim_ids=[],
            created_by_solver_id=created_by_solver_id,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            legacy_import=True,
            provenance={
                "legacy_model": "MemoryEntry",
                "legacy_kind": "evidence",
                "legacy_artifact_ids": list(entry.artifact_ids),
                "confirmation_inferred": False,
            },
        )


class LegacyEvidenceProjection:
    def project_artifact(self, record: LegacyArtifactRecord) -> Artifact:
        return artifact_record_to_artifact(record)

    def project_finding(
        self, finding: LegacyFinding, *, imported_at: str = "legacy:unknown"
    ) -> tuple[EvidenceClaim | None, Finding]:
        return legacy_finding_to_evidence(finding, imported_at=imported_at)


__all__ = [
    "LegacyEvidenceProjection", "LegacyMemoryProjection", "LegacyStrategyProjection",
    "LegacyV5TaskReader",
]
