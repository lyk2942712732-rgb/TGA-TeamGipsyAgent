import json
from pathlib import Path

import pytest

from tga.cli.config_loader import TaskConfigError, load_task_request


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_task_request_builds_the_shared_creation_command(tmp_path: Path):
    path = _write(tmp_path / "task.json", {
        "id": "task_demo",
        "name": "demo",
        "mode": "ctf",
        "goal": "solve",
        "modeOptions": {"flag_format": "flag\\{[^}]+\\}"},
        "input": {"text": "target is http://127.0.0.1:1"},
        "executionPolicy": {
            "preset": "custom",
            "network": {
                "access": "custom",
                "custom_origins": ["http://127.0.0.1:1"],
                "deny_private_networks": False,
                "deny_loopback": False,
                "deny_link_local": False,
            },
        },
    })

    command = load_task_request(path)

    assert command.task_id == "task_demo"
    assert command.mode == "ctf"
    assert command.input_text == "target is http://127.0.0.1:1"
    assert command.execution_policy.network.access == "custom"
    # The CLI never carries a preflight fingerprint; the service issues it.
    assert command.preflight_fingerprint is None


def test_load_task_request_rejects_legacy_target_scope_fields(tmp_path: Path):
    path = _write(tmp_path / "task.json", {
        "name": "demo", "mode": "ctf", "goal": "solve",
        "target": "http://127.0.0.1:1", "scope": ["127.0.0.1:1"],
    })

    with pytest.raises(TaskConfigError, match="unsupported task config fields"):
        load_task_request(path)


def test_load_task_request_rejects_missing_required_field(tmp_path: Path):
    path = _write(tmp_path / "task.json", {"mode": "ctf", "goal": "solve"})

    with pytest.raises(TaskConfigError, match="requires name"):
        load_task_request(path)


def test_load_task_request_rejects_more_than_two_selected_skills(tmp_path: Path):
    path = _write(tmp_path / "task.json", {
        "name": "demo", "mode": "ctf", "goal": "solve",
        "selectedSkills": ["a", "b", "c"],
    })

    with pytest.raises(TaskConfigError, match="at most 2 items"):
        load_task_request(path)


def test_load_task_request_rejects_invalid_json(tmp_path: Path):
    path = tmp_path / "task.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(TaskConfigError, match="invalid JSON"):
        load_task_request(path)


def test_load_task_request_rejects_missing_file(tmp_path: Path):
    with pytest.raises(TaskConfigError, match="not found"):
        load_task_request(tmp_path / "missing.json")

