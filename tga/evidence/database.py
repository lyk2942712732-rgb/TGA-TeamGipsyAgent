"""SQLite connection, schema migration, and rollback-only unit of work."""

from __future__ import annotations

import sqlite3
import json
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
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.row_factory = sqlite3.Row
        try:
            self._reject_unsupported_task_schema()
            schema_path = Path(__file__).with_name("schema.sql")
            self.conn.executescript(schema_path.read_text(encoding="utf-8"))
            self._migrate_schema()
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
            if version != 5:
                raise DatabaseSchemaVersionError(version)

    def transaction(self):
        """Group repository writes into one rollback-only SQLite transaction."""
        database = self

        class UnitOfWork:
            def __enter__(self):
                if database._tx_depth == 0:
                    database.conn.execute("BEGIN IMMEDIATE")
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
        action_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(actions)").fetchall()}
        additions = {
            "strategy_card_id": "TEXT", "strategy_step_id": "TEXT",
            "expected_outcome": "TEXT NOT NULL DEFAULT ''", "retry_reason": "TEXT NOT NULL DEFAULT ''",
            "alternative_analysis": "TEXT NOT NULL DEFAULT ''", "effect_json": "TEXT NOT NULL DEFAULT '{}'",
            "approval_expires_at": "TEXT",
            "input_id": "TEXT", "target_ref": "TEXT", "actual_target": "TEXT",
            "authorization_json": "TEXT NOT NULL DEFAULT '{}'", "provenance_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, declaration in additions.items():
            if name not in action_columns:
                self.conn.execute(f"ALTER TABLE actions ADD COLUMN {name} {declaration}")
        # Destructive legacy cleanup belongs in an explicit migration command,
        # never in a read-path constructor.

    def close(self) -> None:
        self.conn.close()
