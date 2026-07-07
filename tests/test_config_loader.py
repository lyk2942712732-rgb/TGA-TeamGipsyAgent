from pathlib import Path

import pytest

from tga.cli.config_loader import TaskConfigError
from tga.cli.config_loader import load_task_config


def test_load_task_config(tmp_path: Path):
    path = tmp_path / "task.json"
    path.write_text(
        '{"name":"demo","mode":"ctf","target":"http://127.0.0.1:1","scope":["127.0.0.1:1"],"goal":"solve","flag_format":"flag\\\\{[^}]+\\\\}"}',
        encoding="utf-8",
    )
    task = load_task_config(path)
    assert task.id.startswith("task_")


def test_load_task_config_rejects_missing_required_field(tmp_path: Path):
    path = tmp_path / "task.json"
    path.write_text(
        '{"name":"demo","mode":"ctf","target":"http://127.0.0.1:1","scope":["127.0.0.1:1"]}',
        encoding="utf-8",
    )

    with pytest.raises(TaskConfigError, match="invalid task config"):
        load_task_config(path)


def test_load_task_config_rejects_invalid_json(tmp_path: Path):
    path = tmp_path / "task.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(TaskConfigError, match="invalid JSON"):
        load_task_config(path)


def test_load_task_config_rejects_missing_file(tmp_path: Path):
    with pytest.raises(TaskConfigError, match="not found"):
        load_task_config(tmp_path / "missing.json")

