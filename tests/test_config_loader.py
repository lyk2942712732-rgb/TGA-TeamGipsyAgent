from pathlib import Path

from tga.cli.config_loader import load_task_config


def test_load_task_config(tmp_path: Path):
    path = tmp_path / "task.json"
    path.write_text(
        '{"name":"demo","mode":"ctf","target":"http://127.0.0.1:1","scope":["127.0.0.1:1"],"goal":"solve","flag_format":"flag\\\\{[^}]+\\\\}"}',
        encoding="utf-8",
    )
    task = load_task_config(path)
    assert task.id.startswith("task_")

