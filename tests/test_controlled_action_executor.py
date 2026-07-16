from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tga.capabilities.runtime import ControlledActionExecutor
from tga.contracts import ActionSpec, TGATask
from tga.evidence.artifacts import ArtifactStore


def _task() -> TGATask:
    return TGATask(
        id="task_action_executor",
        name="controlled executor",
        mode="ctf",
        target="http://127.0.0.1:1",
        scope=["127.0.0.1:1"],
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


class _FlagHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"flag{candidate_only}"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        return


def test_http_result_exposes_only_candidate_flag(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlagHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        target = f"http://127.0.0.1:{server.server_port}"
        task = _task().model_copy(update={"target": target, "scope": [f"127.0.0.1:{server.server_port}"]})
        executor = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path))

        result = executor.execute(
            task=task,
            action=_action(target=target, arguments={"method": "GET", "path": "/"}),
            workspace=tmp_path / "solver",
        )

        assert result.status == "succeeded"
        assert result.candidate_flags == ["flag{candidate_only}"]
        assert result.candidate_findings == []
        assert result.artifact_ids
    finally:
        server.shutdown()
