from __future__ import annotations

import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
from threading import Thread
import socket

import pytest

from tga.capabilities.http import semantic_fingerprint
from tga.capabilities.runtime import ControlledActionExecutor, ExecutionBudget
from tga.capabilities.schemas import HTTPRequestArguments
from tga.contracts import ActionSpec, TGATask
from tga.evidence.artifacts import ArtifactStore
from tests.runtime_fixtures import execution_policy


def _task() -> TGATask:
    return TGATask(
        id="task_capability",
        name="capability test",
        mode="ctf",
        task_entry_url="http://127.0.0.1:8080/",
        execution_policy=execution_policy(["127.0.0.1:8080"], process=True),
        goal="test controlled execution",
    )


def _action(**updates) -> ActionSpec:
    value = {
        "id": "action_capability",
        "task_id": "task_capability",
        "solver_id": "solver_main",
        "kind": "workspace",
        "capability": "workspace.read",
        "target": "workspace",
        "arguments": {"relative_path": "input.txt"},
        "rationale": "inspect authorized challenge input",
        "risk": "passive",
    }
    value.update(updates)
    return ActionSpec(**value)


def _docker_compute_available() -> bool:
    """Keep Docker-only verification runnable locally and skippable in bare CI."""
    try:
        probe = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


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
        scope = [f"127.0.0.1:{server.server_port}"]
        task = _task().model_copy(update={"task_entry_url": f"{target}/", "execution_policy": execution_policy(scope, process=True)})
        action = _action(kind="http", capability="http.request", target=target, arguments={"path": "/"}, risk="passive")
        result = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path / "artifacts")).execute(task=task, action=action, workspace=tmp_path / "solver")
    finally:
        server.shutdown()
        server.server_close()

    assert result.error and result.error.code == "REDIRECT_OUT_OF_SCOPE"
    assert result.artifact_ids


def test_cross_origin_redirect_strips_explicit_authorization(tmp_path: Path) -> None:
    received: dict[str, str | None] = {}

    class DestinationHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            received["authorization"] = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args) -> None:  # type: ignore[no-untyped-def]
            return

    destination = ThreadingHTTPServer(("127.0.0.1", 0), DestinationHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/final")
            self.end_headers()

        def log_message(self, *_args) -> None:  # type: ignore[no-untyped-def]
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    Thread(target=destination.serve_forever, daemon=True).start()
    Thread(target=redirect.serve_forever, daemon=True).start()
    try:
        policy = execution_policy(
            [f"127.0.0.1:{redirect.server_port}", f"127.0.0.1:{destination.server_port}"],
            process=True,
        )
        policy = policy.model_copy(update={
            "network": policy.network.model_copy(update={"access": "public_internet"}),
        })
        task = _task().model_copy(update={
            "task_entry_url": f"http://127.0.0.1:{redirect.server_port}/",
            "execution_policy": policy,
        })
        action = _action(
            kind="http", capability="http.request",
            target=task.task_entry_url,
            arguments={"path": "/", "headers": {"Authorization": "Bearer must-not-cross-origin"}},
            risk="passive",
        )
        result = ControlledActionExecutor(
            artifact_store=ArtifactStore(tmp_path / "artifacts")
        ).execute(task=task, action=action, workspace=tmp_path / "solver")
    finally:
        redirect.shutdown()
        redirect.server_close()
        destination.shutdown()
        destination.server_close()

    assert result.status == "succeeded"
    assert received["authorization"] is None


def test_task_sources_blocks_cross_origin_redirect_between_seed_origins(tmp_path: Path) -> None:
    class DestinationHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args) -> None:  # type: ignore[no-untyped-def]
            return

    destination = ThreadingHTTPServer(("127.0.0.1", 0), DestinationHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/final")
            self.end_headers()

        def log_message(self, *_args) -> None:  # type: ignore[no-untyped-def]
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    Thread(target=destination.serve_forever, daemon=True).start()
    Thread(target=redirect.serve_forever, daemon=True).start()
    try:
        policy = execution_policy(
            [f"127.0.0.1:{redirect.server_port}", f"127.0.0.1:{destination.server_port}"],
            process=True,
        )
        task = _task().model_copy(update={
            "task_entry_url": f"http://127.0.0.1:{redirect.server_port}/",
            "execution_policy": policy,
        })
        action = _action(
            kind="http", capability="http.request", target=task.task_entry_url,
            arguments={"path": "/"}, risk="passive",
        )
        result = ControlledActionExecutor(
            artifact_store=ArtifactStore(tmp_path / "artifacts")
        ).execute(task=task, action=action, workspace=tmp_path / "solver")
    finally:
        redirect.shutdown()
        redirect.server_close()
        destination.shutdown()
        destination.server_close()

    assert result.error and result.error.code == "REDIRECT_OUT_OF_SCOPE"
    assert result.artifact_ids


def test_http_connection_uses_policy_approved_dns_result(tmp_path: Path, monkeypatch) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"pinned")

        def log_message(self, *_args) -> None:  # type: ignore[no-untyped-def]
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    original = socket.getaddrinfo
    calls = 0

    def one_resolution(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        if host == "pin.test":
            calls += 1
            if calls > 1:
                raise AssertionError("HTTP transport performed an unapproved second DNS lookup")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 0))]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", one_resolution)
    try:
        task = _task().model_copy(update={
            "task_entry_url": f"http://pin.test:{server.server_port}/",
            "execution_policy": execution_policy([f"pin.test:{server.server_port}"], process=True),
        })
        action = _action(
            kind="http", capability="http.request", target=task.task_entry_url,
            arguments={"path": "/"}, risk="passive",
        )
        result = ControlledActionExecutor(
            artifact_store=ArtifactStore(tmp_path / "artifacts")
        ).execute(task=task, action=action, workspace=tmp_path / "solver")
    finally:
        server.shutdown()
        server.server_close()

    assert result.status == "succeeded"
    assert calls == 1


def test_execution_budget_enforces_per_host_rate() -> None:
    budget = ExecutionBudget(http_requests_per_minute=1, http_burst=1)
    http = _action(kind="http", capability="http.request", target="http://127.0.0.1:8080", arguments={"path": "/"})
    assert budget.reserve(http, http_target="http://127.0.0.1:9000/a") is None
    # The action's orchestration target may differ from an allowed absolute
    # request URL. Budgeting must still use the real destination host.
    limited = budget.reserve(http.model_copy(update={"target": "http://127.0.0.1:8080"}), http_target="http://127.0.0.1:9000/b")
    assert limited and limited.code == "RATE_LIMITED" and limited.retryable is True

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
        scope = [f"127.0.0.1:{server.server_port}"]
        task = _task().model_copy(update={"task_entry_url": f"{target}/", "execution_policy": execution_policy(scope, process=True)})
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

    if result.status == "succeeded":
        assert payload["truncated"] is True
        assert len(payload["stdout"].encode()) <= 128
    else:
        assert result.error and result.error.code == "ISOLATED_RUNTIME_UNAVAILABLE"


@pytest.mark.skipif(not _docker_compute_available(), reason="Docker daemon is unavailable")
def test_workspace_python_enforces_real_container_isolation(tmp_path: Path) -> None:
    """Verify the production execution path, rather than only its Docker argv."""
    workspace = tmp_path / "solver"
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "inputs" / "immutable.txt").write_text("input-is-visible", encoding="utf-8")
    (tmp_path / "host-only.txt").write_text("must-not-reach-container", encoding="utf-8")
    source = """
import json
from pathlib import Path
import socket

print("input=" + Path("/workspace/inputs/immutable.txt").read_text())
try:
    Path("/workspace/inputs/immutable.txt").write_text("changed")
except OSError:
    print("input_read_only=true")
else:
    print("input_read_only=false")

Path("/workspace/work/work-result.txt").write_text("work-ok")
Path("/workspace/artifacts/artifact-result.txt").write_text("artifact-ok")
try:
    Path("/workspace/../host-only.txt").read_text()
except OSError:
    print("host_path_hidden=true")
else:
    print("host_path_hidden=false")

try:
    Path("/container-root-write-check").write_text("blocked")
except OSError:
    print("root_read_only=true")
else:
    print("root_read_only=false")

try:
    socket.create_connection(("1.1.1.1", 53), timeout=1)
except OSError:
    print("network_disabled=true")
else:
    print("network_disabled=false")

print("cgroup=" + json.dumps({
    name: Path("/sys/fs/cgroup").joinpath(name).read_text().strip()
    for name in ("memory.max", "pids.max", "cpu.max")
}))
"""
    store = ArtifactStore(tmp_path / "artifacts")
    result = ControlledActionExecutor(artifact_store=store).execute(
        task=_task(),
        action=_action(
            kind="workspace",
            capability="workspace.python",
            target="workspace",
            arguments={"source": source, "timeout": 10},
            risk="active",
        ),
        workspace=workspace,
    )

    assert result.status == "succeeded"
    payload = json.loads(store.read_text(result.artifact_ids[0]))
    assert "input=input-is-visible" in payload["stdout"]
    assert "input_read_only=true" in payload["stdout"]
    assert "host_path_hidden=true" in payload["stdout"]
    assert "root_read_only=true" in payload["stdout"]
    assert "network_disabled=true" in payload["stdout"]
    cgroup_line = next(line for line in payload["stdout"].splitlines() if line.startswith("cgroup="))
    cgroup = json.loads(cgroup_line.removeprefix("cgroup="))
    assert cgroup["memory.max"] == str(512 * 1024 * 1024)
    assert cgroup["pids.max"] == "128"
    assert cgroup["cpu.max"].split() == ["100000", "100000"]
    assert (workspace / "inputs" / "immutable.txt").read_text(encoding="utf-8") == "input-is-visible"
    assert (workspace / "work" / "work-result.txt").read_text(encoding="utf-8") == "work-ok"
    assert (workspace / "artifacts" / "artifact-result.txt").read_text(encoding="utf-8") == "artifact-ok"


@pytest.mark.skipif(not _docker_compute_available(), reason="Docker daemon is unavailable")
def test_workspace_python_enforces_real_timeout_and_output_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "solver"
    store = ArtifactStore(tmp_path / "artifacts")
    executor = ControlledActionExecutor(
        artifact_store=store,
        budget=ExecutionBudget(process_timeout_s=1, max_output_bytes=128),
    )
    result = executor.execute(
        task=_task(),
        action=_action(
            kind="workspace",
            capability="workspace.python",
            target="workspace",
            arguments={"source": "import time\nprint('x' * 4096, flush=True)\ntime.sleep(10)", "timeout": 10},
            risk="active",
        ),
        workspace=workspace,
    )

    assert result.status == "failed"
    assert result.error and result.error.code == "ACTION_TIMEOUT"
    payload = json.loads(store.read_text(result.artifact_ids[0]))
    assert payload["timed_out"] is True
    assert payload["truncated"] is True
    assert len(payload["stdout"].encode()) <= 128
