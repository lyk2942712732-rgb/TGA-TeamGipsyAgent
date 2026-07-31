from __future__ import annotations

from pathlib import Path

from tga.capabilities.runtime import ControlledActionExecutor
from tga.contracts import ActionSpec, TGATask
from tga.evidence.artifacts import ArtifactStore
from tests.runtime_fixtures import execution_policy


def _task() -> TGATask:
    return TGATask(
        id="task_action_executor",
        name="controlled executor",
        mode="ctf",
        task_entry_url="http://127.0.0.1:1/",
        execution_policy=execution_policy(["127.0.0.1:1"]),
        goal="test",
    )


def _action(**updates: object) -> ActionSpec:
    payload = {
        "id": "action_123",
        "task_id": "task_action_executor",
        "solver_id": "solver_123",
        "kind": "http",
        "capability": "http.request",
        "target": "http://127.0.0.1:1/",
        "arguments": {"method": "GET", "path": "/"},
        "rationale": "observe",
        "risk": "passive",
    }
    payload.update(updates)
    return ActionSpec.model_validate(payload)


def test_unknown_capability_is_blocked_and_artifacted(tmp_path: Path) -> None:
    executor = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path))

    result = executor.execute(
        task=_task(),
        action=_action(capability="shell.exec", kind="workspace"),
        workspace=tmp_path / "solver",
    )

    assert result.status == "blocked"
    assert result.error is not None
    assert result.error.code == "UNKNOWN_CAPABILITY"
    assert result.artifact_ids
    assert result.candidate_flags == []


def test_invalid_http_arguments_are_blocked_before_execution(tmp_path: Path) -> None:
    executor = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path))

    result = executor.execute(
        task=_task(),
        action=_action(arguments={"method": "TRACE", "path": "/"}),
        workspace=tmp_path / "solver",
    )

    assert result.status == "blocked"
    assert result.error is not None
    assert result.error.code == "INVALID_ACTION_ARGUMENTS"


def test_http_execution_requires_governed_kali_backend(tmp_path: Path) -> None:
    executor = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path))
    result = executor.execute(
        task=_task(),
        action=_action(),
        workspace=tmp_path / "solver",
    )

    assert result.status == "blocked"
    assert result.error is not None
    assert result.error.code == "EXECUTION_BACKEND_REQUIRED"
    assert result.artifact_ids
