from __future__ import annotations

from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from tga.capabilities.http import semantic_fingerprint
from tga.capabilities.runtime import ControlledActionExecutor, ExecutionBudget
from tga.capabilities.schemas import HTTPRequestArguments
from tga.contracts import ActionSpec, TGATask
from tga.evidence.artifacts import ArtifactStore


def _task() -> TGATask:
    return TGATask(
        id="task_capability",
        name="capability test",
        mode="ctf",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        goal="test controlled execution",
    )


def _action(**updates) -> ActionSpec:
    value = {
        "id": "action_capability",
        "task_id": "task_capability",
        "solver_id": "solver_main",
        "hypothesis_id": "hyp_1",
        "kind": "workspace",
        "capability": "workspace.read",
        "target": "workspace",
        "arguments": {"relative_path": "input.txt"},
        "rationale": "inspect authorized challenge input",
        "risk": "passive",
    }
    value.update(updates)
    return ActionSpec(**value)


def test_registry_rejects_unknown_capability_with_artifact(tmp_path: Path) -> None:
    executor = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path / "artifacts"))
    action = _action(capability="shell.run", kind="workspace")

    result = executor.execute(task=_task(), action=action, workspace=tmp_path / "solver")

    assert result.error and result.error.code == "UNKNOWN_CAPABILITY"
    assert result.artifact_ids


def test_workspace_cannot_escape_solver_directory(tmp_path: Path) -> None:
    executor = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path / "artifacts"))
    action = _action(arguments={"relative_path": "../outside.txt"})

    result = executor.execute(task=_task(), action=action, workspace=tmp_path / "solver")

    assert result.error and result.error.code == "WORKSPACE_PATH_DENIED"
    assert result.artifact_ids


def test_dynamic_timestamp_has_same_http_semantic_fingerprint() -> None:
    action = _action(kind="http", capability="http.request", target="http://127.0.0.1:8080", arguments={}, risk="passive")
    first = semantic_fingerprint(action=action, args=HTTPRequestArguments(path="/check?ts=100&user=alice"), url="http://127.0.0.1:8080/check?ts=100&user=alice")
    second = semantic_fingerprint(action=action, args=HTTPRequestArguments(path="/check?ts=200&user=alice"), url="http://127.0.0.1:8080/check?ts=200&user=alice")

    assert first == second


def test_distinct_http_form_values_have_distinct_semantic_fingerprints() -> None:
    action = _action(kind="http", capability="http.request", target="http://127.0.0.1:8080", arguments={}, risk="active")
    first = semantic_fingerprint(
        action=action,
        args=HTTPRequestArguments(method="POST", path="/run", body={"code": "phpinfo();"}),
        url="http://127.0.0.1:8080/run",
    )
    second = semantic_fingerprint(
        action=action,
        args=HTTPRequestArguments(method="POST", path="/run", body={"code": "getenv();"}),
        url="http://127.0.0.1:8080/run",
    )

    assert first != second


def test_tool_runner_unavailable_returns_structured_result(tmp_path: Path) -> None:
    executor = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path / "artifacts"))
    action = _action(
        kind="tool",
        capability="tool.invoke",
        target="http://127.0.0.1:8080",
        arguments={"tool_id": "sqlmap", "tool_method": "sql_scan"},
        risk="active",
    )

    result = executor.execute(task=_task(), action=action, workspace=tmp_path / "solver")

    assert result.error and result.error.code == "TOOL_RUNNER_UNAVAILABLE"
    assert result.artifact_ids


def test_http_redirect_outside_scope_is_blocked_and_artifacted(tmp_path: Path) -> None:
    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://localhost:65534/outside")
            self.end_headers()

        def log_message(self, *_args) -> None:  # type: ignore[no-untyped-def]
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        target = f"http://127.0.0.1:{server.server_port}"
        task = _task().model_copy(update={"target": target, "scope": [f"127.0.0.1:{server.server_port}"]})
        action = _action(kind="http", capability="http.request", target=target, arguments={"path": "/"}, risk="passive")
        result = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path / "artifacts")).execute(task=task, action=action, workspace=tmp_path / "solver")
    finally:
        server.shutdown()
        server.server_close()

    assert result.error and result.error.code == "REDIRECT_OUT_OF_SCOPE"
    assert result.artifact_ids


def test_execution_budget_enforces_per_host_rate_and_mcp_concurrency() -> None:
    budget = ExecutionBudget(http_requests_per_minute=1, http_burst=1, max_mcp_concurrency=1)
    http = _action(kind="http", capability="http.request", target="http://127.0.0.1:8080", arguments={"path": "/"})
    assert budget.reserve(http, http_target="http://127.0.0.1:9000/a") is None
    # The action's orchestration target may differ from an allowed absolute
    # request URL. Budgeting must still use the real destination host.
    assert budget.reserve(http.model_copy(update={"target": "http://127.0.0.1:8080"}), http_target="http://127.0.0.1:9000/b").code == "ACTION_BUDGET_EXCEEDED"  # type: ignore[union-attr]

    first_tool = _action(id="tool_1", kind="tool", capability="tool.invoke", target="http://127.0.0.1:8080", arguments={"tool_id": "nmap", "tool_method": "scan"}, risk="active")
    second_tool = first_tool.model_copy(update={"id": "tool_2"})
    assert budget.reserve(first_tool) is None
    assert budget.reserve(second_tool).code == "ACTION_BUDGET_EXCEEDED"  # type: ignore[union-attr]
    budget.release(first_tool)
    assert budget.reserve(second_tool) is None


def test_http_output_is_bounded_before_artifact_serialization(tmp_path: Path) -> None:
    class LargeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"x" * 1024
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:  # type: ignore[no-untyped-def]
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), LargeHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        target = f"http://127.0.0.1:{server.server_port}"
        task = _task().model_copy(update={"target": target, "scope": [f"127.0.0.1:{server.server_port}"]})
        store = ArtifactStore(tmp_path / "artifacts")
        executor = ControlledActionExecutor(artifact_store=store, budget=ExecutionBudget(max_output_bytes=128))
        action = _action(kind="http", capability="http.request", target=target, arguments={"path": "/"})
        result = executor.execute(task=task, action=action, workspace=tmp_path / "solver")
        payload = __import__("json").loads(store.read_text(result.artifact_ids[0]))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["truncated"] is True
    assert len(payload["body_excerpt"].encode()) <= 128


def test_workspace_python_output_is_stream_bounded(tmp_path: Path) -> None:
    task = _task()
    store = ArtifactStore(tmp_path / "artifacts")
    executor = ControlledActionExecutor(artifact_store=store, budget=ExecutionBudget(max_output_bytes=128))
    action = _action(
        kind="workspace",
        capability="workspace.python",
        target="workspace",
        arguments={"source": "print('x' * 4096)", "timeout": 10},
        risk="active",
    )

    result = executor.execute(task=task, action=action, workspace=tmp_path / "solver")
    payload = __import__("json").loads(store.read_text(result.artifact_ids[0]))

    assert result.status == "succeeded"
    assert payload["truncated"] is True
    assert len(payload["stdout"].encode()) <= 128
