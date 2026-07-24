from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.migrate_schema_v4_to_v5 import MigrationError, migrate_database
from tga.contracts import TGATask


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_schema_v4_to_v5.py"


def _file_payload(asset_id: str, name: str, relative_path: str, content: bytes, kind: str) -> dict:
    stored_name = f"{asset_id.removeprefix('asset_')}{Path(name).suffix}"
    return {
        "id": asset_id,
        "originalName": name,
        "storedName": stored_name,
        "relativePath": relative_path,
        "mimeType": "text/plain",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "kind": kind,
        "mediaKind": "text",
    }


def _create_v4(
    root: Path,
    *,
    prompt: str = "Inspect https://Challenge.Example:443/path?q=1 and http://docs.example/a.",
    target: str = "https://fallback.example/legacy",
    status: str = "created",
    schema: int = 4,
    policy: dict | None = None,
) -> tuple[Path, dict[str, bytes]]:
    workspace = root / "workspace"
    task_content = b"task material"
    hint_content = b"hint material"
    sources = {
        "inputs/task/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt": task_content,
        "inputs/hints/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.txt": hint_content,
    }
    for relative, content in sources.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    task_file = _file_payload(
        "asset_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "task.txt",
        "inputs/task/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt",
        task_content,
        "task",
    )
    hint_file = _file_payload(
        "asset_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "hint.txt",
        "inputs/hints/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.txt",
        hint_content,
        "hint",
    )
    old_policy = policy or {
        "network": {
            "mode": "observe",
            "allowed_scopes": ["challenge.example", "http://docs.example"],
            "rate_limit": 0,
            "concurrency": 0,
        },
        "filesystem": {"mode": "workspace_write", "allowed_roots": ["workspace"]},
        "process_execution": {"mode": "sandbox_only", "timeout_seconds": 60},
        "fuzzing": {"mode": "disabled", "max_cases": 0, "max_duration_seconds": 0, "concurrency": 0},
        "state_change": {"mode": "forbidden", "allowed_actions": []},
        "containment": {"mode": "observe_only", "allowed_actions": []},
        "mcp": {"enabled_servers": [], "enabled_tools": [], "enabled_resources": [], "allow_active": False},
        "source": "user",
    }
    payload = {
        "id": "migration_task",
        "name": "Migration task",
        "mode": "ctf",
        "target": target,
        "targets": [],
        "hints": [],
        "session_input": {"taskFiles": [task_file], "hint": {"text": prompt, "files": [hint_file]}},
        "mcp_capabilities": {"catalog_version": "mcp_empty", "server_ids": [], "tools": [], "created_at": None},
        "scope": [],
        "target_theme": "",
        "target_description": "",
        "intensity": "normal",
        "allow_active_scan": False,
        "mcp_servers": [],
        "mcp_direct_tools": [],
        "goal": "Solve the challenge",
        "flag_format": r"FLAG\{[^}]+\}",
        "mode_config": {
            "mode": "ctf",
            "subtype": "auto",
            "flag_format": r"FLAG\{[^}]+\}",
            "expected_flag_count": 1,
            "verifier": {"kind": "local_regex", "tool_ref": None},
            "deadline": None,
            "alternative_proof": None,
        },
        "execution_policy": old_policy,
        "execution_budget": {},
        "migration_notes": [],
        "insecure_tls_origins": [],
        "schema_version": schema,
    }
    database = root / "evidence.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);"
        "CREATE TABLE sessions (task_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, "
        "status TEXT NOT NULL, active_solver_id TEXT, turn_count INTEGER NOT NULL DEFAULT 0, "
        "max_turns INTEGER NOT NULL, started_at TEXT, finished_at TEXT, stop_reason TEXT NOT NULL DEFAULT '', "
        "workspace_path TEXT NOT NULL DEFAULT '', mcp_catalog_version TEXT NOT NULL DEFAULT '');"
    )
    connection.execute(
        "INSERT INTO tasks(id,payload_json,created_at) VALUES (?,?,?)",
        (payload["id"], json.dumps(payload), "2026-01-01T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO sessions(task_id,schema_version,status,max_turns,workspace_path) VALUES (?,?,?,?,?)",
        (payload["id"], schema, status, 48, "workspace"),
    )
    connection.commit()
    connection.close()
    return database, sources


def _read_task(database: Path) -> tuple[dict, int]:
    connection = sqlite3.connect(database)
    task = json.loads(connection.execute("SELECT payload_json FROM tasks").fetchone()[0])
    session_schema = connection.execute("SELECT schema_version FROM sessions").fetchone()[0]
    connection.close()
    return task, session_schema


def test_explicit_cli_migrates_database_files_urls_and_creates_unique_backup(tmp_path: Path) -> None:
    database, sources = _create_v4(tmp_path)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(database)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    task, session_schema = _read_task(database)
    validated = TGATask.model_validate(task)
    assert validated.schema_version == session_schema == 5
    assert validated.session_input.prompt.startswith("Inspect https://")
    assert [item.id for item in validated.session_input.files] == [
        "asset_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "asset_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    assert all(item.kind == "task_input" for item in validated.session_input.files)
    assert validated.task_entry_url == "https://challenge.example:443/path?q=1"
    assert validated.execution_policy.network.seed_origins == [
        "https://challenge.example",
        "http://docs.example",
        "https://fallback.example",
    ]
    assert validated.execution_policy.network.custom_origins == [
        "https://challenge.example",
        "http://docs.example",
    ]
    assert validated.execution_policy.network.rate_limit_per_minute == 1
    assert validated.execution_policy.network.concurrency == 1
    assert validated.execution_policy.local_compute.mode == "isolated"
    assert validated.execution_policy.preset == "custom"
    for item, expected in zip(validated.session_input.files, sources.values(), strict=True):
        assert (tmp_path / "workspace" / item.relative_path).read_bytes() == expected
    backups = list(tmp_path.glob("evidence.db.v4-backup-*"))
    assert len(backups) == 1
    old_task, old_session_schema = _read_task(backups[0])
    assert old_task["schema_version"] == old_session_schema == 4
    assert "migration complete" in completed.stdout

    migrated_bytes = database.read_bytes()
    rerun = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(database)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rerun.returncode == 1
    assert "UNSUPPORTED_SCHEMA" in rerun.stderr
    assert database.read_bytes() == migrated_bytes
    assert len(list(tmp_path.glob("evidence.db.v4-backup-*"))) == 1


def test_importing_migrator_does_not_read_or_modify_database(tmp_path: Path) -> None:
    database, _ = _create_v4(tmp_path)
    before = database.read_bytes()

    imported = subprocess.run(
        [sys.executable, "-c", "import scripts.migrate_schema_v4_to_v5"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert imported.returncode == 0, imported.stderr
    assert database.read_bytes() == before
    assert not list(tmp_path.glob("evidence.db.v4-backup-*"))


def test_prompt_url_precedes_target_and_credential_url_is_ignored(tmp_path: Path) -> None:
    secret = "do-not-print-token"
    database, _ = _create_v4(
        tmp_path,
        prompt=f"Ignore https://user:{secret}@secret.example/a then use https://safe.example/path.",
        target="https://fallback.example/target",
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(database)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    task, _ = _read_task(database)
    assert task["task_entry_url"] == "https://safe.example/path"
    assert task["execution_policy"]["network"]["seed_origins"] == [
        "https://safe.example",
        "https://fallback.example",
    ]
    assert secret not in completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("old_policy", "expected"),
    [
        (
            {
                "network": {"mode": "none", "allowed_scopes": [], "rate_limit": 30, "concurrency": 2},
                "process_execution": {"mode": "forbidden", "timeout_seconds": 0},
                "fuzzing": {"mode": "disabled"},
                "state_change": {"mode": "forbidden", "allowed_actions": []},
                "containment": {"mode": "observe_only", "allowed_actions": []},
            },
            ("disabled", "observe", "disabled", "forbidden", "custom"),
        ),
        (
            {
                "network": {"mode": "interact", "allowed_scopes": ["*"], "rate_limit": 30, "concurrency": 2},
                "process_execution": {"mode": "authorized_host", "timeout_seconds": 90},
                "fuzzing": {"mode": "bounded"},
                "state_change": {"mode": "authorized", "allowed_actions": ["submission"]},
                "containment": {"mode": "observe_only", "allowed_actions": []},
            },
            ("public_internet", "interact", "isolated", "approval_required", "autonomous_ctf"),
        ),
        (
            {
                "network": {"mode": "observe", "allowed_scopes": [], "rate_limit": 30, "concurrency": 2},
                "process_execution": {"mode": "sandbox_only", "timeout_seconds": 120},
                "fuzzing": {"mode": "disabled"},
                "state_change": {"mode": "authorized", "allowed_actions": ["resource_create"]},
                "containment": {"mode": "authorized", "allowed_actions": ["isolate_host"]},
            },
            ("task_sources", "observe", "isolated", "allowlisted", "custom"),
        ),
    ],
)
def test_legacy_policy_mapping(tmp_path: Path, old_policy: dict, expected: tuple[str, ...]) -> None:
    database, _ = _create_v4(tmp_path, policy=old_policy)

    migrate_database(database)

    task, _ = _read_task(database)
    policy = task["execution_policy"]
    assert (
        policy["network"]["access"],
        policy["network"]["interaction"],
        policy["local_compute"]["mode"],
        policy["high_impact"]["mode"],
        policy["preset"],
    ) == expected


@pytest.mark.parametrize(
    ("schema", "status", "code"),
    [(3, "created", "UNSUPPORTED_SCHEMA"), (5, "created", "UNSUPPORTED_SCHEMA"), (4, "running", "SESSION_ACTIVE")],
)
def test_unsupported_or_active_sources_are_rejected_without_mutation(
    tmp_path: Path, schema: int, status: str, code: str
) -> None:
    database, sources = _create_v4(tmp_path, schema=schema, status=status)
    before_db = database.read_bytes()
    before_files = {name: (tmp_path / "workspace" / name).read_bytes() for name in sources}

    with pytest.raises(MigrationError) as raised:
        migrate_database(database)

    assert raised.value.code == code
    assert database.read_bytes() == before_db
    assert before_files == {name: (tmp_path / "workspace" / name).read_bytes() for name in sources}
    assert not (tmp_path / "workspace" / "inputs" / "files").exists()
    assert not list(tmp_path.glob("evidence.db.v4-backup-*"))


@pytest.mark.parametrize(
    "fault_point",
    ["after_backup", "after_files_staged", "before_publish", "after_files_published"],
)
def test_injected_failure_rolls_back_database_and_published_files(tmp_path: Path, fault_point: str) -> None:
    database, sources = _create_v4(tmp_path)
    before_db = database.read_bytes()
    before_task, before_schema = _read_task(database)
    before_files = {name: (tmp_path / "workspace" / name).read_bytes() for name in sources}

    def fail(point: str) -> None:
        if point == fault_point:
            raise RuntimeError("injected secret should not be surfaced")

    with pytest.raises(RuntimeError, match="injected secret"):
        migrate_database(database, fault_hook=fail)

    assert database.read_bytes() == before_db
    assert _read_task(database) == (before_task, before_schema)
    assert before_files == {name: (tmp_path / "workspace" / name).read_bytes() for name in sources}
    assert not (tmp_path / "workspace" / "inputs" / "files").exists()
    assert not list((tmp_path / "workspace" / "inputs").glob(".schema-v5-files-*"))
    assert not list(tmp_path.glob(".evidence.db.schema-v5-*.tmp"))
    backups = list(tmp_path.glob("evidence.db.v4-backup-*"))
    assert len(backups) == 1
    assert _read_task(backups[0]) == (before_task, before_schema)


def test_checksum_failure_preserves_source_data_and_sanitizes_cli_output(tmp_path: Path) -> None:
    secret = "API_KEY=super-secret-value"
    database, sources = _create_v4(tmp_path, prompt=secret)
    corrupt = tmp_path / "workspace" / next(iter(sources))
    corrupt.write_bytes(b"changed")
    before_task, before_schema = _read_task(database)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(database)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "INPUT_CHECKSUM_MISMATCH" in completed.stderr
    assert secret not in completed.stdout + completed.stderr
    assert _read_task(database) == (before_task, before_schema)
    assert corrupt.read_bytes() == b"changed"
    assert not (tmp_path / "workspace" / "inputs" / "files").exists()
