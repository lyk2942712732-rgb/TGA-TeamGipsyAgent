from __future__ import annotations

import sqlite3
from pathlib import Path

from tga.evidence.store import EvidenceStore


def test_existing_session_table_is_additively_migrated_for_workspace_and_catalog(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE sessions ("
        "task_id TEXT PRIMARY KEY, status TEXT NOT NULL, active_solver_id TEXT, "
        "turn_count INTEGER NOT NULL DEFAULT 0, max_turns INTEGER NOT NULL, "
        "started_at TEXT, finished_at TEXT, stop_reason TEXT NOT NULL DEFAULT ''"
        ")"
    )
    connection.execute(
        "INSERT INTO sessions(task_id,status,turn_count,max_turns,stop_reason) VALUES ('legacy','created',0,48,'')"
    )
    connection.commit()
    connection.close()

    store = EvidenceStore(database)
    try:
        columns = {row["name"] for row in store.conn.execute("PRAGMA table_info(sessions)")}
        session = store.get_session("legacy")
        assert {"schema_version", "workspace_path", "mcp_catalog_version"}.issubset(columns)
        assert session is not None
        assert session.schema_version == 2
        assert session.workspace_path == ""
        assert session.mcp_catalog_version == ""
    finally:
        store.close()
