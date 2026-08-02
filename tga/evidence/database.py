"""SQLite connection, schema loading, and rollback-only unit of work."""

from __future__ import annotations

import sqlite3
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 6
SCHEMA_PATH = (
    Path(__file__).parents[1] / "infrastructure" / "persistence" / "schema_v6.sql"
)
# Tables that only ever existed before schema v6.  Their presence means the
# file was created by an older build and must go through `tga migrate`.
PRE_V6_TABLES = frozenset({
    "solvers", "memory_entries", "actions", "strategy_cards", "events",
    "artifact_indexes",
})


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DatabaseSchemaVersionError(ValueError):
    def __init__(self, schema_version: int):
        super().__init__(f"task schema {schema_version} is not executable")
        self.schema_version = schema_version


class DatabaseSchemaMismatchError(ValueError):
    """Raised when an existing database does not match the v6 physical schema."""

    code = "DATABASE_SCHEMA_MISMATCH"

    def __init__(self, detail: str):
        super().__init__(
            f"database physical schema is not schema v6 ({detail}); "
            "rebuild it with `tga migrate --apply`"
        )
        self.detail = detail


class Database:
    """Own one schema-v6 SQLite connection.

    Opening a database never alters its structure.  An empty file receives the
    single authoritative `schema_v6.sql`; an existing file is validated and
    rejected when it was built by an older schema.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._tx_depth = 0
        self._tx_rollback_only = False
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.row_factory = sqlite3.Row
        try:
            self._load_schema()
        except Exception:
            self.conn.close()
            raise

    def _load_schema(self) -> None:
        """Create a fresh v6 database, or validate an existing one."""
        if self._is_empty():
            self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self.conn.execute(
                "INSERT INTO schema_metadata(version,applied_at,content_sha256) "
                "VALUES (?,?,?)",
                (SCHEMA_VERSION, utc_now(), schema_content_hash()),
            )
            self._commit()
            return
        self._reject_pre_v6_database()
        self._reject_unsupported_task_schema()

    def _is_empty(self) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
        return int(row["total"]) == 0

    def _reject_pre_v6_database(self) -> None:
        names = {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        legacy = sorted(PRE_V6_TABLES.intersection(names))
        if legacy:
            raise DatabaseSchemaMismatchError(
                f"pre-v6 tables present: {', '.join(legacy)}"
            )
        if "schema_metadata" not in names:
            raise DatabaseSchemaMismatchError("schema_metadata table is missing")
        row = self.conn.execute(
            "SELECT version,content_sha256 FROM schema_metadata "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is None or int(row["version"]) != SCHEMA_VERSION:
            raise DatabaseSchemaMismatchError("schema_metadata does not record version 6")
        if str(row["content_sha256"]) != schema_content_hash():
            raise DatabaseSchemaMismatchError("schema content hash does not match schema_v6.sql")

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
            if version != SCHEMA_VERSION:
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

    def close(self) -> None:
        self.conn.close()


def schema_content_hash() -> str:
    """Content hash of the single authoritative schema file."""
    return hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


def apply_schema(conn: sqlite3.Connection) -> str:
    """Apply schema_v6.sql to an empty connection and record its hash."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    content_hash = schema_content_hash()
    conn.execute(
        "INSERT INTO schema_metadata(version,applied_at,content_sha256) VALUES (?,?,?) "
        "ON CONFLICT(version) DO UPDATE SET applied_at=excluded.applied_at,"
        "content_sha256=excluded.content_sha256",
        (SCHEMA_VERSION, utc_now(), content_hash),
    )
    return content_hash
