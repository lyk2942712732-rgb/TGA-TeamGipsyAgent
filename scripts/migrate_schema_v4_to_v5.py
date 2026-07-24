"""Explicit, offline migration of one TGA task database from schema 4 to 5."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

# Keep the documented direct command usable without installing the repository.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tga.contracts import TGATask
from tga.network_policy import extract_input_urls, normalize_origin


SOURCE_SCHEMA = 4
TARGET_SCHEMA = 5
ACTIVE_SESSION_STATUSES = {"running", "awaiting_approval"}
_STORED_NAME = re.compile(r"^[a-f0-9]{32,64}(?:\.[A-Za-z0-9]{1,16})?$")
_ASSET_ID = re.compile(r"^asset_[a-f0-9]{16,64}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class MigrationError(RuntimeError):
    """A migration failure whose message is safe to show to an operator."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MigrationError:
    return MigrationError(code, message)


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 1000")
    return connection


def _load_source(connection: sqlite3.Connection) -> tuple[dict[str, Any], sqlite3.Row]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tasks','sessions')"
        )
    }
    if tables != {"tasks", "sessions"}:
        raise _fail("INVALID_DATABASE", "database must contain tasks and sessions tables")
    rows = connection.execute("SELECT id, payload_json FROM tasks ORDER BY id").fetchall()
    if len(rows) != 1:
        raise _fail("AMBIGUOUS_DATABASE", "database must contain exactly one task")
    try:
        payload = json.loads(rows[0]["payload_json"])
        source_schema = int(payload.get("schema_version") or 0)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _fail("INVALID_TASK", "task payload is not valid schema 4 JSON") from exc
    if source_schema != SOURCE_SCHEMA:
        raise _fail(
            "UNSUPPORTED_SCHEMA",
            f"expected task schema {SOURCE_SCHEMA}; found schema {source_schema}",
        )
    session_rows = connection.execute(
        "SELECT * FROM sessions WHERE task_id=?", (rows[0]["id"],)
    ).fetchall()
    if len(session_rows) != 1:
        raise _fail("INVALID_SESSION", "task must have exactly one matching session")
    session = session_rows[0]
    if int(session["schema_version"] or 0) != SOURCE_SCHEMA:
        raise _fail("INVALID_SESSION", "task and session schema versions must both be 4")
    if str(session["status"] or "") in ACTIVE_SESSION_STATUSES:
        raise _fail("SESSION_ACTIVE", "running or awaiting-approval sessions cannot be migrated")
    return payload, session


def _workspace_for(db_path: Path, session: sqlite3.Row) -> Path:
    task_root = db_path.parent.resolve()
    relative = str(session["workspace_path"] or "workspace")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise _fail("INVALID_WORKSPACE", "session workspace must be task-relative")
    workspace = (task_root / candidate).resolve()
    try:
        workspace.relative_to(task_root)
    except ValueError as exc:
        raise _fail("INVALID_WORKSPACE", "session workspace escapes the task directory") from exc
    if not workspace.is_dir():
        raise _fail("INVALID_WORKSPACE", "session workspace does not exist")
    return workspace


def _value(item: dict[str, Any], snake: str, camel: str | None = None) -> Any:
    if snake in item:
        return item[snake]
    return item.get(camel or snake)


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _safe_source(workspace: Path, relative_path: str) -> Path:
    normalized = relative_path.replace("\\", "/")
    if not re.fullmatch(r"inputs/(?:task|hints)/[^/]+", normalized):
        raise _fail("INVALID_INPUT_PATH", "legacy input path is outside task or hint inputs")
    source = (workspace / normalized).resolve()
    try:
        source.relative_to(workspace.resolve())
    except ValueError as exc:
        raise _fail("INVALID_INPUT_PATH", "legacy input path escapes the workspace") from exc
    if not source.is_file():
        raise _fail("INPUT_MISSING", "a legacy input file is missing")
    return source


def _migrate_file(item: dict[str, Any], workspace: Path, stage: Path) -> dict[str, Any]:
    asset_id = str(_value(item, "id") or "")
    stored_name = str(_value(item, "stored_name", "storedName") or "")
    relative_path = str(_value(item, "relative_path", "relativePath") or "")
    expected_hash = str(_value(item, "sha256") or "").lower()
    expected_size = _value(item, "size")
    if not _ASSET_ID.fullmatch(asset_id) or not _STORED_NAME.fullmatch(stored_name):
        raise _fail("INVALID_INPUT_METADATA", "legacy input identity or stored name is invalid")
    if not _SHA256.fullmatch(expected_hash) or not isinstance(expected_size, int) or expected_size < 0:
        raise _fail("INVALID_INPUT_METADATA", "legacy input size or checksum is invalid")
    source = _safe_source(workspace, relative_path)
    actual_size, actual_hash = _hash_file(source)
    if actual_size != expected_size or actual_hash != expected_hash:
        raise _fail("INPUT_CHECKSUM_MISMATCH", "legacy input does not match persisted metadata")

    destination = stage / stored_name
    if destination.exists():
        duplicate_size, duplicate_hash = _hash_file(destination)
        if (duplicate_size, duplicate_hash) != (actual_size, actual_hash):
            raise _fail("INPUT_NAME_COLLISION", "legacy inputs have a conflicting stored name")
    else:
        shutil.copyfile(source, destination)
        copied_size, copied_hash = _hash_file(destination)
        if (copied_size, copied_hash) != (actual_size, actual_hash):
            raise _fail("INPUT_COPY_FAILED", "staged input verification failed")

    provenance = _value(item, "provenance")
    if not isinstance(provenance, dict) or provenance.get("source") not in {
        "user_upload",
        "manual",
        "mcp",
        "generated",
    }:
        provenance = {
            "source": "user_upload",
            "created_at": None,
            "original_name": str(_value(item, "original_name", "originalName") or ""),
            "parent_input_id": None,
        }
    return {
        "id": asset_id,
        "original_name": str(_value(item, "original_name", "originalName") or ""),
        "stored_name": stored_name,
        "relative_path": f"inputs/files/{stored_name}",
        "mime_type": str(_value(item, "mime_type", "mimeType") or "application/octet-stream"),
        "size": actual_size,
        "sha256": actual_hash,
        "kind": "task_input",
        "media_kind": str(_value(item, "media_kind", "mediaKind") or "other"),
        "provenance": provenance,
    }


def _url_inputs(prompt: str, old_target: str) -> tuple[list[str], str | None]:
    urls = extract_input_urls(prompt)
    for candidate in extract_input_urls(old_target):
        if candidate not in urls:
            urls.append(candidate)
    origins: list[str] = []
    for url in urls:
        try:
            origin = normalize_origin(url)
        except ValueError:
            continue
        if origin not in origins:
            origins.append(origin)
    return origins, urls[0] if urls else None


def _scope_origins(scopes: list[Any], seed_origins: list[str]) -> tuple[list[str], bool]:
    result: list[str] = []
    public = False
    for raw in scopes:
        value = str(raw or "").strip()
        if not value:
            continue
        if value == "*":
            public = True
            continue
        candidates: list[str] = []
        if value.startswith(("http://", "https://")):
            candidates = [value]
        elif re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", value):
            host = value.rsplit(":", 1)[0].casefold()
            matching = [origin for origin in seed_origins if (urlsplit(origin).hostname or "").casefold() == host]
            candidates = matching or [f"http://{value}", f"https://{value}"]
        else:
            raise _fail("UNSUPPORTED_SCOPE", "legacy network scope cannot be represented safely in schema 5")
        for candidate in candidates:
            try:
                origin = normalize_origin(candidate)
            except ValueError as exc:
                raise _fail("UNSUPPORTED_SCOPE", "legacy network scope is invalid") from exc
            if origin not in result:
                result.append(origin)
    return result, public


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(high, max(low, parsed))


def _map_policy(old: Any, *, mode: str, seed_origins: list[str]) -> dict[str, Any]:
    policy = old if isinstance(old, dict) else {}
    old_network = policy.get("network") if isinstance(policy.get("network"), dict) else {}
    network_mode = str(old_network.get("mode") or "none")
    custom_origins, public_scope = _scope_origins(
        list(old_network.get("allowed_scopes") or []), seed_origins
    )
    if network_mode not in {"observe", "interact"}:
        access = "disabled"
    elif public_scope:
        access = "public_internet"
    elif custom_origins:
        access = "custom"
    elif seed_origins:
        access = "task_sources"
    else:
        access = "disabled"
    network = {
        "access": access,
        "interaction": "interact" if network_mode == "interact" else "observe",
        "seed_origins": seed_origins,
        "custom_origins": custom_origins,
        "deny_private_networks": True,
        "deny_loopback": True,
        "deny_link_local": True,
        "deny_cloud_metadata": True,
        "rate_limit_per_minute": _bounded_int(old_network.get("rate_limit"), 30, 1, 100_000),
        "concurrency": _bounded_int(old_network.get("concurrency"), 2, 1, 128),
        "request_timeout_seconds": 30,
    }

    process = policy.get("process_execution") if isinstance(policy.get("process_execution"), dict) else {}
    process_mode = str(process.get("mode") or "forbidden")
    local_compute = {
        "mode": "isolated" if process_mode in {"sandbox_only", "authorized_host"} else "disabled",
        "timeout_seconds": _bounded_int(process.get("timeout_seconds"), 120, 1, 3600),
        "concurrency": 2,
        "network_inheritance": "task_network_policy",
    }

    state = policy.get("state_change") if isinstance(policy.get("state_change"), dict) else {}
    containment = policy.get("containment") if isinstance(policy.get("containment"), dict) else {}
    fuzzing = policy.get("fuzzing") if isinstance(policy.get("fuzzing"), dict) else {}
    allowed_actions = list(
        dict.fromkeys(
            str(item).strip()
            for item in [*(state.get("allowed_actions") or []), *(containment.get("allowed_actions") or [])]
            if str(item).strip()
        )
    )
    approval = (
        state.get("mode") == "approval_required"
        or containment.get("mode") == "approval_required"
        or fuzzing.get("mode") in {"bounded", "extended"}
    )
    authorized = state.get("mode") == "authorized" or containment.get("mode") == "authorized"
    if approval:
        impact_mode = "approval_required"
    elif authorized and allowed_actions:
        impact_mode = "allowlisted"
    else:
        impact_mode = "forbidden"
        allowed_actions = []
    high_impact = {"mode": impact_mode, "allowed_actions": allowed_actions}

    if access == "disabled" and local_compute["mode"] == "isolated" and impact_mode == "forbidden":
        preset = "offline_analysis"
    elif (
        access == "task_sources"
        and network["interaction"] == "observe"
        and local_compute["mode"] == "isolated"
        and impact_mode == "forbidden"
    ):
        preset = "safe_observation"
    elif (
        mode == "ctf"
        and access == "public_internet"
        and network["interaction"] == "interact"
        and local_compute["mode"] == "isolated"
        and impact_mode == "approval_required"
    ):
        preset = "autonomous_ctf"
    else:
        preset = "custom"
    return {
        "preset": preset,
        "network": network,
        "local_compute": local_compute,
        "high_impact": high_impact,
    }


def _transform_task(payload: dict[str, Any], workspace: Path, stage: Path) -> dict[str, Any]:
    old_input = payload.get("session_input")
    if not isinstance(old_input, dict):
        raise _fail("INVALID_INPUT", "schema 4 session_input is missing")
    hint = old_input.get("hint") if isinstance(old_input.get("hint"), dict) else {}
    prompt = str(hint.get("text") or "").strip()
    old_target = str(payload.get("target") or "")
    seed_origins, entry_url = _url_inputs(prompt, old_target)
    task_files = _value(old_input, "task_files", "taskFiles") or []
    hint_files = hint.get("files") or []
    if not isinstance(task_files, list) or not isinstance(hint_files, list):
        raise _fail("INVALID_INPUT", "schema 4 file lists are invalid")
    files = [_migrate_file(item, workspace, stage) for item in [*task_files, *hint_files]]

    migrated = {
        key: payload[key]
        for key in (
            "id",
            "name",
            "mode",
            "mcp_capabilities",
            "goal",
            "flag_format",
            "mode_config",
            "execution_budget",
            "insecure_tls_origins",
        )
        if key in payload
    }
    migrated.update(
        {
            "session_input": {"prompt": prompt, "files": files},
            "task_entry_url": entry_url,
            "execution_policy": _map_policy(
                payload.get("execution_policy"),
                mode=str(payload.get("mode") or "ctf"),
                seed_origins=seed_origins,
            ),
            "schema_version": TARGET_SCHEMA,
        }
    )
    try:
        return TGATask.model_validate(migrated).model_dump(mode="json")
    except Exception as exc:
        raise _fail("V5_VALIDATION_FAILED", "migrated task does not satisfy schema 5") from exc


def _backup_database(source: sqlite3.Connection, db_path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = db_path.with_name(f"{db_path.name}.v4-backup-{stamp}-{uuid4().hex[:8]}")
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise _fail("BACKUP_FAILED", "database backup failed integrity validation")
    except Exception:
        destination.close()
        backup.unlink(missing_ok=True)
        raise
    destination.close()
    return backup


def migrate_database(
    db_path: str | Path,
    *,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Migrate one database; importing this module never invokes it."""

    database = Path(db_path).resolve()
    if not database.is_file():
        raise _fail("DATABASE_NOT_FOUND", "database file does not exist")
    read_connection = _connect(database, read_only=True)
    try:
        source_payload, source_session = _load_source(read_connection)
        workspace = _workspace_for(database, source_session)
    finally:
        read_connection.close()

    destination_files = workspace / "inputs" / "files"
    if destination_files.exists():
        raise _fail("DESTINATION_EXISTS", "workspace/inputs/files already exists")
    token = uuid4().hex
    stage = destination_files.parent / f".schema-v5-files-{token}"
    temporary_db = database.with_name(f".{database.name}.schema-v5-{token}.tmp")
    backup: Path | None = None
    files_published = False
    source = _connect(database)
    try:
        source_data_version = int(source.execute("PRAGMA data_version").fetchone()[0])
        backup = _backup_database(source, database)
        backup_connection = _connect(backup, read_only=True)
        try:
            backup_payload, backup_session = _load_source(backup_connection)
        finally:
            backup_connection.close()
        if backup_payload != source_payload or dict(backup_session) != dict(source_session):
            raise _fail("SOURCE_CHANGED", "database changed while the backup was created")
        if fault_hook:
            fault_hook("after_backup")
        try:
            source.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            raise _fail("DATABASE_BUSY", "database is in use; stop the task runtime and retry") from exc
        locked_payload, locked_session = _load_source(source)
        locked_data_version = int(source.execute("PRAGMA data_version").fetchone()[0])
        if (
            locked_data_version != source_data_version
            or locked_payload != source_payload
            or dict(locked_session) != dict(source_session)
        ):
            raise _fail("SOURCE_CHANGED", "database changed during migration preparation")

        shutil.copyfile(backup, temporary_db)
        destination_files.parent.mkdir(parents=True, exist_ok=True)
        stage.mkdir()
        migrated = _transform_task(source_payload, workspace, stage)
        if fault_hook:
            fault_hook("after_files_staged")

        temporary = _connect(temporary_db)
        try:
            temporary.execute("BEGIN IMMEDIATE")
            temporary.execute(
                "UPDATE tasks SET payload_json=? WHERE id=?",
                (json.dumps(migrated, ensure_ascii=False, separators=(",", ":")), migrated["id"]),
            )
            cursor = temporary.execute(
                "UPDATE sessions SET schema_version=? WHERE task_id=? AND schema_version=?",
                (TARGET_SCHEMA, migrated["id"], SOURCE_SCHEMA),
            )
            if cursor.rowcount != 1:
                raise _fail("SESSION_UPDATE_FAILED", "session schema version could not be updated")
            temporary.execute("COMMIT")
            if temporary.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise _fail("MIGRATED_DB_INVALID", "migrated database failed integrity validation")
            validated_payload, validated_session = _load_v5(temporary)
            if validated_payload["id"] != migrated["id"] or int(validated_session["schema_version"]) != 5:
                raise _fail("MIGRATED_DB_INVALID", "migrated database validation failed")
        except Exception:
            if temporary.in_transaction:
                temporary.execute("ROLLBACK")
            raise
        finally:
            temporary.close()
        if fault_hook:
            fault_hook("before_publish")

        os.replace(stage, destination_files)
        files_published = True
        if fault_hook:
            fault_hook("after_files_published")
        source.execute("ROLLBACK")
        source.close()
        source = None
        os.replace(temporary_db, database)
    except Exception:
        if source is not None:
            if source.in_transaction:
                source.execute("ROLLBACK")
            source.close()
            source = None
        if files_published:
            shutil.rmtree(destination_files, ignore_errors=True)
        shutil.rmtree(stage, ignore_errors=True)
        temporary_db.unlink(missing_ok=True)
        raise
    finally:
        if source is not None:
            if source.in_transaction:
                source.execute("ROLLBACK")
            source.close()

    return {
        "database": str(database),
        "backup": str(backup),
        "task_id": str(source_payload["id"]),
        "files": len(migrated["session_input"]["files"]),
        "source_schema": SOURCE_SCHEMA,
        "target_schema": TARGET_SCHEMA,
    }


def _load_v5(connection: sqlite3.Connection) -> tuple[dict[str, Any], sqlite3.Row]:
    rows = connection.execute("SELECT id, payload_json FROM tasks").fetchall()
    if len(rows) != 1:
        raise _fail("MIGRATED_DB_INVALID", "migrated database has an invalid task count")
    try:
        payload = json.loads(rows[0]["payload_json"])
        TGATask.model_validate(payload)
    except Exception as exc:
        raise _fail("MIGRATED_DB_INVALID", "migrated task failed schema 5 validation") from exc
    if int(payload.get("schema_version") or 0) != TARGET_SCHEMA:
        raise _fail("MIGRATED_DB_INVALID", "migrated task is not schema 5")
    sessions = connection.execute("SELECT * FROM sessions WHERE task_id=?", (rows[0]["id"],)).fetchall()
    if len(sessions) != 1 or int(sessions[0]["schema_version"] or 0) != TARGET_SCHEMA:
        raise _fail("MIGRATED_DB_INVALID", "migrated session is not schema 5")
    return payload, sessions[0]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicitly migrate one offline TGA evidence.db from schema 4 to schema 5."
    )
    parser.add_argument("--db", required=True, type=Path, help="path to the task evidence.db")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = migrate_database(args.db)
    except MigrationError as exc:
        print(f"migration failed [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("migration failed [INTERNAL_ERROR]: no changes were committed", file=sys.stderr)
        return 1
    print(
        "migration complete: "
        f"task={result['task_id']} schema={result['source_schema']}->{result['target_schema']} "
        f"files={result['files']} backup={result['backup']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
