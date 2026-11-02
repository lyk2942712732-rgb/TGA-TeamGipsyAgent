import json
from pathlib import Path
from uuid import uuid4

import pytest

from tga3.config import TGA3Config
from tga3.task_storage import KINDS, migration_plan, relocate_path
from tga3.workers.action_journal import ActionJournal


def configuration(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "data/tasks")
    for kind, attribute in KINDS.items():
        setattr(config.runtime, attribute, str(tmp_path / "data" / kind))
    return config


def test_task_directories_are_complete_isolated_and_cannot_escape(tmp_path):
    config = configuration(tmp_path)
    first, second = uuid4(), uuid4()
    config.ensure_task_directories(first)
    assert {p.name for p in config.task_path(first).iterdir()} == set(KINDS)
    assert config.task_path(first, "inputs") != config.task_path(second, "inputs")
    with pytest.raises(ValueError):
        config.task_path("../other", "inputs")
    with pytest.raises(ValueError):
        config.task_path(first, "../other")
    with pytest.raises(ValueError):
        config.task_path(first, "workspaces", "../worker")


def test_migration_preserves_nested_file_paths_and_rejects_collisions(tmp_path):
    config = configuration(tmp_path)
    task_id = uuid4()
    old = config.resolve_path(config.runtime.workspace_root) / str(task_id)
    raw = old / "scout/.codex/session.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_text("original")
    plan = migration_plan(config)
    assert relocate_path(str(raw), plan) == str(config.task_path(task_id, "workspaces") / "scout/.codex/session.jsonl")
    unrelated = str(tmp_path / "another-task")
    assert relocate_path(unrelated, plan) == unrelated
    config.task_path(task_id, "workspaces").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        migration_plan(config)
    assert raw.read_text() == "original"


def test_migration_refuses_overlapping_roots(tmp_path):
    config = configuration(tmp_path)
    config.runtime.task_root = str(tmp_path / "data")
    with pytest.raises(ValueError, match="overlap"):
        migration_plan(config)


def test_execution_record_keeps_pending_state_full_output_and_redacts_credentials(tmp_path):
    journal = ActionJournal(tmp_path, identity={"agent_id": "worker"}, secret='key"private')
    journal.record("tool-1", "shell", inputs={"command": "print fixture"})
    path = next(tmp_path.glob("*.json"))
    pending = json.loads(path.read_text(encoding="utf-8"))
    assert pending["status"] == "started"
    output = "x" * 20_000 + 'key"private'
    journal.record("tool-1", "shell", result={"output": output, "exit_code": 7}, status="failed")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert record["input"]["command"] == "print fixture"
    assert record["status"] == "failed" and record["result"]["exit_code"] == 7
    assert record["result"]["output"] == "x" * 20_000 + "[redacted]"
    # A later CLI cycle can reuse a native tool id without overwriting history.
    journal.record("tool-1", "shell", inputs={"command": "next cycle"})
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_read_only_extraction_does_not_write_execution_records(tmp_path):
    journal = ActionJournal(tmp_path / "readonly", identity={}, enabled=False)
    journal.record("tool-1", "read", result={"text": "fixture"}, status="completed")
    assert not (tmp_path / "readonly").exists()


def test_migration_renames_task_artifacts_to_logs_and_relocates_evidence_paths(tmp_path):
    config = configuration(tmp_path)
    task_id = uuid4()
    old = config.task_path(task_id) / "artifacts"
    raw = old / "scout/action-1.json"
    raw.parent.mkdir(parents=True)
    raw.write_text('{"result":{"output":"original"}}')
    plan = migration_plan(config)
    assert plan == [(old, config.task_path(task_id, "logs"))]
    assert relocate_path(str(raw), plan) == str(config.task_path(task_id, "logs", "scout") / raw.name)
    assert raw.read_text() == '{"result":{"output":"original"}}'
    config.task_path(task_id, "logs").mkdir()
    with pytest.raises(FileExistsError):
        migration_plan(config)



def test_execution_log_tracks_modified_and_partial_outputs_without_claiming_unchanged_files(tmp_path):
    output = tmp_path / "art"
    output.mkdir()
    (output / "existing.txt").write_text("old")
    (output / "untouched.txt").write_text("untouched")
    journal = ActionJournal(tmp_path / "logs", identity={}, output_directory=output)
    journal.record("redirect", "shell", inputs={"command": "fixture > existing.txt"})
    (output / "existing.txt").write_text("new body")
    (output / "nested").mkdir()
    (output / "nested/new.txt").write_text("partial body")
    journal.interrupt_pending()
    record = json.loads(next((tmp_path / "logs").glob("*.json")).read_text(encoding="utf-8"))
    assert record["status"] == "interrupted"
    assert record["output_paths"] == ["existing.txt", "nested/new.txt"]
    assert not journal.pending
