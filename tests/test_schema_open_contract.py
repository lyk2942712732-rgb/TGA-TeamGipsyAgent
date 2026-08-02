"""Opening a database must load one schema, never migrate structure."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tga.evidence.database import (
    Database,
    DatabaseSchemaMismatchError,
    schema_content_hash,
)
from tga.evidence.store import EvidenceStore


def _object_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def test_new_database_applies_exactly_one_schema_file(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence.db")
    try:
        recorded = store.conn.execute(
            "SELECT version,content_sha256 FROM schema_metadata"
        ).fetchall()
        names = _object_names(store.conn)
    finally:
        store.close()

    assert [(int(row[0]), str(row[1])) for row in recorded] == [
        (6, schema_content_hash())
    ]
    # Legacy physical tables must not be created by the v6 schema.
    assert not names.intersection({
        "solvers", "memory_entries", "actions", "strategy_cards", "events",
        "artifact_indexes",
    })


def test_reopening_a_database_executes_no_ddl(tmp_path: Path) -> None:
    path = tmp_path / "evidence.db"
    store = EvidenceStore(path)
    try:
        before = _object_names(store.conn)
        applied_at = store.conn.execute(
            "SELECT applied_at FROM schema_metadata WHERE version=6"
        ).fetchone()[0]
    finally:
        store.close()

    reopened = EvidenceStore(path)
    try:
        assert _object_names(reopened.conn) == before
        # An unchanged schema is never re-stamped, so opening is side-effect free.
        assert reopened.conn.execute(
            "SELECT applied_at FROM schema_metadata WHERE version=6"
        ).fetchone()[0] == applied_at
    finally:
        reopened.close()


def test_pre_v6_database_is_rejected_instead_of_being_patched(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE sessions ("
        "task_id TEXT PRIMARY KEY, status TEXT NOT NULL, active_solver_id TEXT, "
        "turn_count INTEGER NOT NULL DEFAULT 0, max_turns INTEGER NOT NULL, "
        "started_at TEXT, finished_at TEXT, stop_reason TEXT NOT NULL DEFAULT ''"
        ")"
    )
    connection.execute("CREATE TABLE solvers(id TEXT PRIMARY KEY)")
    connection.commit()
    before = _object_names(connection)
    connection.close()

    with pytest.raises(DatabaseSchemaMismatchError, match="pre-v6 tables present"):
        Database(database)

    verify = sqlite3.connect(database)
    try:
        # The rejected database is left exactly as it was found.
        assert _object_names(verify) == before
    finally:
        verify.close()


def test_database_without_schema_metadata_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "partial.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    with pytest.raises(DatabaseSchemaMismatchError, match="schema_metadata"):
        Database(database)
