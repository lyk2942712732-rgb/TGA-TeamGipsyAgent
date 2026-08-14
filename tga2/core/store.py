"""One compact SQLite store for TGA business state; Graph checkpoints stay separate."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

from tga2.core.models import (
    AgentEvent,
    Artifact,
    EvidenceClaim,
    Finding,
    Intent,
    Plan,
    SolverRun,
    Task,
    TaskStatus,
    utc_now,
)
from tga2.core.policy import ExecutionPolicy, ToolAction

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class TaskStore:
    """Thread-safe repository facade scoped to one task database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                yield self.conn
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def create_task(self, task: Task, policy: ExecutionPolicy) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO tasks(id,task_json,policy_json,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    task.id,
                    task.model_dump_json(),
                    policy.model_dump_json(),
                    task.status.value,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )

    def get_task(self, task_id: str) -> Task | None:
        row = self._one("SELECT task_json,status FROM tasks WHERE id=?", (task_id,))
        if row is None:
            return None
        task = Task.model_validate_json(row["task_json"])
        return task.model_copy(update={"status": TaskStatus(row["status"])})

    def get_policy(self, task_id: str) -> ExecutionPolicy:
        row = self._one("SELECT policy_json FROM tasks WHERE id=?", (task_id,))
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        return ExecutionPolicy.model_validate_json(row["policy_json"])

    def set_task_status(self, task_id: str, status: TaskStatus) -> None:
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT task_json FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            task = Task.model_validate_json(row["task_json"]).model_copy(
                update={"status": status, "updated_at": now}
            )
            conn.execute(
                "UPDATE tasks SET task_json=?,status=?,updated_at=? WHERE id=?",
                (task.model_dump_json(), status.value, now.isoformat(), task_id),
            )

    def save_plan(self, plan: Plan) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO plans(task_id,version,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(task_id,version) DO UPDATE SET "
                "payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                (
                    plan.task_id,
                    plan.version,
                    plan.model_dump_json(),
                    plan.created_at.isoformat(),
                    plan.updated_at.isoformat(),
                ),
            )
            for intent in plan.intents:
                if intent.task_id != plan.task_id:
                    raise ValueError("intent belongs to another task")
                conn.execute(
                    "INSERT INTO intents(id,task_id,payload_json,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "payload_json=excluded.payload_json,status=excluded.status,updated_at=excluded.updated_at",
                    (
                        intent.id,
                        intent.task_id,
                        intent.model_dump_json(),
                        intent.status.value,
                        intent.created_at.isoformat(),
                        intent.updated_at.isoformat(),
                    ),
                )

    def get_plan(self, task_id: str) -> Plan | None:
        row = self._one(
            "SELECT payload_json FROM plans WHERE task_id=? ORDER BY version DESC LIMIT 1",
            (task_id,),
        )
        return Plan.model_validate_json(row["payload_json"]) if row else None

    def list_plans(self, task_id: str) -> list[Plan]:
        return [
            Plan.model_validate_json(row["payload_json"])
            for row in self._all(
                "SELECT payload_json FROM plans WHERE task_id=? ORDER BY version",
                (task_id,),
            )
        ]

    def list_intents(self, task_id: str) -> list[Intent]:
        return [
            Intent.model_validate_json(row["payload_json"])
            for row in self._all(
                "SELECT payload_json FROM intents WHERE task_id=? ORDER BY created_at,id",
                (task_id,),
            )
        ]

    def update_intent(self, intent: Intent) -> None:
        with self.transaction() as conn:
            result = conn.execute(
                "UPDATE intents SET payload_json=?,status=?,updated_at=? WHERE id=? AND task_id=?",
                (
                    intent.model_dump_json(),
                    intent.status.value,
                    intent.updated_at.isoformat(),
                    intent.id,
                    intent.task_id,
                ),
            )
            if result.rowcount != 1:
                raise KeyError(f"intent not found: {intent.id}")

    def save_solver_run(self, run: SolverRun) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO solver_runs(id,task_id,role,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                (
                    run.id,
                    run.task_id,
                    run.role.value,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )

    def list_solver_runs(self, task_id: str) -> list[SolverRun]:
        return [
            SolverRun.model_validate_json(row["payload_json"])
            for row in self._all(
                "SELECT payload_json FROM solver_runs WHERE task_id=? ORDER BY created_at",
                (task_id,),
            )
        ]

    def save_artifact(self, artifact: Artifact) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO artifacts(id,task_id,payload_json,sha256,created_at) VALUES (?,?,?,?,?)",
                (
                    artifact.id,
                    artifact.task_id,
                    artifact.model_dump_json(),
                    artifact.sha256,
                    artifact.created_at.isoformat(),
                ),
            )

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        row = self._one("SELECT payload_json FROM artifacts WHERE id=?", (artifact_id,))
        return Artifact.model_validate_json(row["payload_json"]) if row else None

    def list_artifacts(self, task_id: str) -> list[Artifact]:
        return [
            Artifact.model_validate_json(row["payload_json"])
            for row in self._all(
                "SELECT payload_json FROM artifacts WHERE task_id=? ORDER BY created_at",
                (task_id,),
            )
        ]

    def save_claim(self, claim: EvidenceClaim) -> None:
        artifact = self.get_artifact(claim.artifact_id)
        if artifact is None:
            raise ValueError("evidence claim references a missing artifact")
        if artifact.task_id != claim.task_id:
            raise ValueError("evidence claim and artifact belong to different tasks")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO evidence_claims(id,task_id,artifact_id,payload_json,status,created_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "payload_json=excluded.payload_json,status=excluded.status",
                (
                    claim.id,
                    claim.task_id,
                    claim.artifact_id,
                    claim.model_dump_json(),
                    claim.status,
                    claim.created_at.isoformat(),
                ),
            )

    def get_claim(self, claim_id: str) -> EvidenceClaim | None:
        row = self._one(
            "SELECT payload_json FROM evidence_claims WHERE id=?", (claim_id,)
        )
        return EvidenceClaim.model_validate_json(row["payload_json"]) if row else None

    def list_claims(self, task_id: str) -> list[EvidenceClaim]:
        return [
            EvidenceClaim.model_validate_json(row["payload_json"])
            for row in self._all(
                "SELECT payload_json FROM evidence_claims WHERE task_id=? ORDER BY created_at",
                (task_id,),
            )
        ]

    def save_finding(self, finding: Finding) -> None:
        if any(
            self.get_claim(claim_id) is None for claim_id in finding.evidence_claim_ids
        ):
            raise ValueError("finding references a missing evidence claim")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO findings(id,task_id,payload_json,status,severity,created_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "payload_json=excluded.payload_json,status=excluded.status,severity=excluded.severity",
                (
                    finding.id,
                    finding.task_id,
                    finding.model_dump_json(),
                    finding.status,
                    finding.severity,
                    finding.created_at.isoformat(),
                ),
            )

    def list_findings(self, task_id: str) -> list[Finding]:
        return [
            Finding.model_validate_json(row["payload_json"])
            for row in self._all(
                "SELECT payload_json FROM findings WHERE task_id=? ORDER BY created_at",
                (task_id,),
            )
        ]

    def save_action(self, action: ToolAction) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO tool_actions(id,task_id,payload_json,tool_name,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "payload_json=excluded.payload_json,status=excluded.status,updated_at=excluded.updated_at",
                (
                    action.id,
                    action.task_id,
                    action.model_dump_json(),
                    action.tool_name,
                    action.status,
                    action.created_at.isoformat(),
                    action.updated_at.isoformat(),
                ),
            )

    def list_actions(self, task_id: str) -> list[ToolAction]:
        return [
            ToolAction.model_validate_json(row["payload_json"])
            for row in self._all(
                "SELECT payload_json FROM tool_actions WHERE task_id=? ORDER BY created_at",
                (task_id,),
            )
        ]

    def get_action(self, action_id: str) -> ToolAction | None:
        row = self._one(
            "SELECT payload_json FROM tool_actions WHERE id=?", (action_id,)
        )
        return ToolAction.model_validate_json(row["payload_json"]) if row else None

    def append_event(self, event: AgentEvent) -> AgentEvent:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO events(task_id,type,solver_id,intent_id,payload_json,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    event.task_id,
                    event.type,
                    event.solver_id,
                    event.intent_id,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.created_at.isoformat(),
                ),
            )
            return event.model_copy(update={"seq": int(cursor.lastrowid)})

    def list_events(
        self, task_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[AgentEvent]:
        return [
            AgentEvent(
                seq=row["seq"],
                task_id=row["task_id"],
                type=row["type"],
                solver_id=row["solver_id"],
                intent_id=row["intent_id"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in self._all(
                "SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                (task_id, max(0, after_seq), max(1, min(limit, 1000))),
            )
        ]

    def save_report(self, task_id: str, markdown: str, path: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO reports(task_id,markdown,path,created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET markdown=excluded.markdown,path=excluded.path,"
                "created_at=excluded.created_at",
                (task_id, markdown, path, utc_now().isoformat()),
            )

    def get_report(self, task_id: str) -> dict[str, str] | None:
        row = self._one(
            "SELECT markdown,path,created_at FROM reports WHERE task_id=?", (task_id,)
        )
        return dict(row) if row else None

    def snapshot(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        plan = self.get_plan(task_id)
        return {
            "schema_version": 1,
            "task": task.model_dump(mode="json"),
            "policy": self.get_policy(task_id).model_dump(mode="json"),
            "plan": plan.model_dump(mode="json") if plan else None,
            "intents": [
                item.model_dump(mode="json") for item in self.list_intents(task_id)
            ],
            "solver_runs": [
                item.model_dump(mode="json") for item in self.list_solver_runs(task_id)
            ],
            "artifacts": [
                item.model_dump(mode="json") for item in self.list_artifacts(task_id)
            ],
            "evidence_claims": [
                item.model_dump(mode="json") for item in self.list_claims(task_id)
            ],
            "findings": [
                item.model_dump(mode="json") for item in self.list_findings(task_id)
            ],
            "actions": [
                item.model_dump(mode="json") for item in self.list_actions(task_id)
            ],
            "report": self.get_report(task_id),
        }

    def _one(self, sql: str, parameters: tuple[Any, ...]) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, parameters).fetchone()

    def _all(self, sql: str, parameters: tuple[Any, ...]) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, parameters).fetchall()


__all__ = ["TaskStore"]
