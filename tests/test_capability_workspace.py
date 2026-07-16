from __future__ import annotations

import json
from pathlib import Path

from tga.capabilities.executor import CapabilityExecutor
from tga.capabilities.models import ActionSpec


def make_spec(capability, *, action_id="action_1", arguments=None, scope=None, flag_format=None):
    return ActionSpec(
        task_id="task_ws",
        solver_id="solver_a",
        action_id=action_id,
        capability=capability,
        target="",
        scope=scope or [],
        flag_format=flag_format,
        arguments=arguments or {},
    )


def test_workspace_python_success_timeout_block_and_truncate(tmp_path):
    executor = CapabilityExecutor(run_root=tmp_path)
    secret = tmp_path / "task_ws" / "solvers" / "solver_a" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("flag{secret}", encoding="utf-8")

    ok = executor.execute(
        make_spec(
            "workspace.python",
            action_id="py_ok",
            flag_format=r"flag\{[^}]+\}",
            arguments={"code": "print('flag{py_ok}')"},
        )
    )
    timed_out = executor.execute(
        make_spec(
            "workspace.python",
            action_id="py_timeout",
            arguments={"code": "while True: pass", "timeout_seconds": 1},
        )
    )
    blocked = executor.execute(
        make_spec(
            "workspace.python",
            action_id="py_blocked",
            arguments={"code": "import socket\nprint('nope')"},
        )
    )
    truncated = executor.execute(
        make_spec(
            "workspace.python",
            action_id="py_truncated",
            arguments={"code": "print('A' * 5000)", "max_output_bytes": 1024},
        )
    )
    denied_read = executor.execute(
        make_spec(
            "workspace.python",
            action_id="py_denied_read",
            arguments={"code": "print(open('../secret.txt', encoding='utf-8').read())"},
        )
    )

    assert ok.status == "ok"
    assert ok.candidate_flags == ["flag{py_ok}"]
    assert timed_out.status == "timeout"
    assert blocked.status == "blocked"
    assert blocked.error and blocked.error.code == "WORKSPACE_PYTHON_DENIED"
    assert truncated.output_truncated
    assert denied_read.status == "blocked"
    assert denied_read.error and denied_read.error.code == "WORKSPACE_PYTHON_DENIED"


def test_workspace_python_sanitizes_run_path_segments(tmp_path):
    executor = CapabilityExecutor(run_root=tmp_path)
    result = executor.execute(
        ActionSpec(
            task_id="../escape_task",
            solver_id="..\\escape_solver",
            action_id="../escape_action",
            capability="workspace.python",
            arguments={"code": "print('ok')"},
        )
    )

    assert result.status == "ok"
    assert not (tmp_path.parent / "escape_task").exists()
    assert list(tmp_path.rglob("artifact_*.json"))


def test_workspace_binary_reads_only_workspace_or_scoped_paths(tmp_path):
    executor = CapabilityExecutor(run_root=tmp_path)
    workspace_file = tmp_path / "task_ws" / "solvers" / "solver_a" / "workspace" / "sample.bin"
    workspace_file.parent.mkdir(parents=True)
    workspace_file.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 10 + b"\x3e\x00hello flag{bin}")

    ok = executor.execute(
        make_spec(
            "workspace.binary",
            action_id="bin_ok",
            flag_format=r"flag\{[^}]+\}",
            arguments={"path": "sample.bin", "operation": "strings"},
        )
    )
    blocked = executor.execute(
        make_spec(
            "workspace.binary",
            action_id="bin_blocked",
            arguments={"path": str(tmp_path.parent / "outside.bin"), "operation": "metadata"},
        )
    )
    truncated = executor.execute(
        make_spec(
            "workspace.binary",
            action_id="bin_truncated",
            arguments={"path": "sample.bin", "operation": "hexdump", "length": 4096, "max_output_bytes": 1024},
        )
    )

    assert ok.status == "ok"
    assert ok.candidate_flags == ["flag{bin}"]
    assert blocked.status == "blocked"
    assert blocked.error and blocked.error.code == "WORKSPACE_PATH_DENIED"
    assert truncated.status == "ok"


def test_artifact_inspect_is_solver_scoped_and_can_truncate(tmp_path):
    executor = CapabilityExecutor(run_root=tmp_path)
    artifact_dir = tmp_path / "task_ws" / "solvers" / "solver_a" / "artifacts"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "artifact_manual.txt"
    artifact.write_text("prefix flag{artifact} " + "A" * 5000, encoding="utf-8")
    other_solver = tmp_path / "task_ws" / "solvers" / "solver_b" / "artifacts" / "secret.txt"
    other_solver.parent.mkdir(parents=True)
    other_solver.write_text("flag{other}", encoding="utf-8")

    ok = executor.execute(
        make_spec(
            "artifact.inspect",
            action_id="inspect_ok",
            flag_format=r"flag\{[^}]+\}",
            arguments={
                "artifact_path": artifact.name,
                "keywords": ["flag{artifact}"],
                "length": 4096,
                "max_output_bytes": 1024,
            },
        )
    )
    blocked = executor.execute(
        make_spec(
            "artifact.inspect",
            action_id="inspect_blocked",
            arguments={"artifact_path": str(other_solver)},
        )
    )

    body = (artifact_dir / ok.artifacts[0].path).read_text(encoding="utf-8")
    payload = json.loads(body)
    assert ok.status == "ok"
    assert ok.candidate_flags == ["flag{artifact}"]
    assert ok.output_truncated
    assert payload["keyword_hits"]
    assert blocked.status == "blocked"
    assert blocked.error and blocked.error.code == "ARTIFACT_NOT_FOUND"
