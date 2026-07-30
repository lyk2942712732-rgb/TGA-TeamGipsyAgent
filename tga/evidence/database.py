"""SQLite connection, schema migration, and rollback-only unit of work."""

from __future__ import annotations

import sqlite3
import json
import time
from datetime import UTC, datetime
from pathlib import Path


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DatabaseSchemaVersionError(ValueError):
    def __init__(self, schema_version: int):
        super().__init__(f"task schema {schema_version} is not executable")
        self.schema_version = schema_version


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._tx_depth = 0
        self._tx_rollback_only = False
        self.conn.execute("PRAGMA busy_timeout = 1000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.row_factory = sqlite3.Row
        try:
            self._reject_unsupported_task_schema()
            schema_path = Path(__file__).with_name("schema.sql")
            self.conn.executescript(schema_path.read_text(encoding="utf-8"))
            self._migrate_schema()
            v6_schema = Path(__file__).parents[1] / "infrastructure" / "persistence" / "schema_v6.sql"
            self.conn.executescript(v6_schema.read_text(encoding="utf-8"))
            schema_hash = __import__("hashlib").sha256(v6_schema.read_bytes()).hexdigest()
            self.conn.execute(
                "INSERT INTO schema_metadata(version,applied_at,content_sha256) VALUES (6,?,?) "
                "ON CONFLICT(version) DO UPDATE SET "
                "applied_at=excluded.applied_at,content_sha256=excluded.content_sha256 "
                "WHERE schema_metadata.content_sha256<>excluded.content_sha256",
                (utc_now(), schema_hash),
            )
            self._commit()
        except Exception:
            self.conn.close()
            raise

    def _reject_unsupported_task_schema(self) -> None:
        exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        if not exists:
            return
        for row in self.conn.execute("SELECT payload_json FROM tasks").fetchall():
            try:
                version = int(json.loads(row["payload_json"]).get("schema_version") or 0)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise DatabaseSchemaVersionError(0) from exc
            if version not in {5, 6}:
                raise DatabaseSchemaVersionError(version)

    def transaction(self):
        """Group repository writes into one rollback-only SQLite transaction."""
        database = self

        class UnitOfWork:
            def __enter__(self):
                if database._tx_depth == 0:
                    started = time.perf_counter()
                    retry_count = 0
                    for attempt in range(4):
                        try:
                            database.conn.execute("BEGIN IMMEDIATE")
                            retry_count = attempt
                            break
                        except sqlite3.OperationalError as exc:
                            if "locked" not in str(exc).casefold() or attempt == 3:
                                raise
                            time.sleep(0.01 * (2 ** attempt))
                    wait_ms = (time.perf_counter() - started) * 1000
                    metric_table = database.conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='db_write_lock_metrics'"
                    ).fetchone()
                    if metric_table is not None and (retry_count or wait_ms >= 1):
                        database.conn.execute(
                            "INSERT INTO db_write_lock_metrics(wait_ms,retry_count,recorded_at) "
                            "VALUES (?,?,?)",
                            (round(wait_ms, 3), retry_count, utc_now()),
                        )
                    database._tx_rollback_only = False
                database._tx_depth += 1
                return database

            def __exit__(self, exc_type, exc, tb):
                if exc_type is not None:
                    database._tx_rollback_only = True
                database._tx_depth -= 1
                if database._tx_depth == 0:
                    try:
                        if database._tx_rollback_only:
                            database.conn.rollback()
                        else:
                            database.conn.commit()
                    finally:
                        database._tx_rollback_only = False
                return False

        return UnitOfWork()

    def _commit(self) -> None:
        if self._tx_depth == 0:
            self.conn.commit()

    def _migrate_schema(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "schema_version" not in columns:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 2")
        if "workspace_path" not in columns:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN workspace_path TEXT NOT NULL DEFAULT ''")
        if "mcp_catalog_version" not in columns:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN mcp_catalog_version TEXT NOT NULL DEFAULT ''")
        event_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(agent_events)").fetchall()}
        if "schema_version" not in event_columns:
            self.conn.execute("ALTER TABLE agent_events ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 2")
        if "intent_id" not in event_columns:
            self.conn.execute("ALTER TABLE agent_events ADD COLUMN intent_id TEXT")
        action_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(actions)").fetchall()}
        additions = {
            "strategy_card_id": "TEXT", "strategy_step_id": "TEXT",
            "expected_outcome": "TEXT NOT NULL DEFAULT ''", "retry_reason": "TEXT NOT NULL DEFAULT ''",
            "alternative_analysis": "TEXT NOT NULL DEFAULT ''", "effect_json": "TEXT NOT NULL DEFAULT '{}'",
            "approval_expires_at": "TEXT",
            "input_id": "TEXT", "target_ref": "TEXT", "actual_target": "TEXT",
            "authorization_json": "TEXT NOT NULL DEFAULT '{}'", "provenance_json": "TEXT NOT NULL DEFAULT '{}'",
            "intent_id": "TEXT", "local_plan_step_id": "TEXT",
            "execution_policy_snapshot_id": "TEXT", "solver_tool_policy_snapshot_id": "TEXT",
            "governed_action_id": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in action_columns:
                self.conn.execute(f"ALTER TABLE actions ADD COLUMN {name} {declaration}")
        table_additions = {
            "intents": {
                "global_plan_id": "TEXT",
                "assigned_solver_id": "TEXT",
                "priority": "INTEGER NOT NULL DEFAULT 0",
                "version": "INTEGER NOT NULL DEFAULT 1",
                "claimed_at": "TEXT",
                "schema_version": "INTEGER NOT NULL DEFAULT 5",
            },
            "artifacts": {"schema_version": "INTEGER NOT NULL DEFAULT 5"},
            "findings": {"schema_version": "INTEGER NOT NULL DEFAULT 5"},
            "solver_leases": {
                "fencing_token": "INTEGER NOT NULL DEFAULT 1",
                "renewed_at": "TEXT NOT NULL DEFAULT ''",
            },
            "knowledge_items": {
                "subject": "TEXT",
                "structured_value": "TEXT",
            },
            "governed_actions": {
                "attempt": "INTEGER NOT NULL DEFAULT 1",
            },
        }
        for table, requested in table_additions.items():
            table_exists = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if table_exists is None:
                continue
            existing = {
                row["name"]
                for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, declaration in requested.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        # Destructive legacy cleanup belongs in an explicit migration command,
        # never in a read-path constructor.

    def close(self) -> None:
        self.conn.close()
