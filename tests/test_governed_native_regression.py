from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tga.capabilities.runtime import ControlledActionExecutor, ExecutionBudget
from tga.contracts import TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tests.runtime_fixtures import execution_policy


def test_legacy_executor_never_performs_http_network_io(tmp_path):
    class NeverCalled(BaseHTTPRequestHandler):
        calls = 0
        def do_POST(self):  # noqa: N802
            type(self).calls += 1; self.send_response(200); self.end_headers()
        def log_message(self, *_): return

    server = ThreadingHTTPServer(("127.0.0.1", 0), NeverCalled)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from tga.contracts import ActionSpec
        base = f"http://127.0.0.1:{server.server_port}"
        task = TGATask(id="preflight", name="preflight", mode="ctf", task_entry_url=f"{base}/", goal="test", execution_policy=execution_policy([base]))
        executor = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path / "artifacts"), budget=ExecutionBudget(http_requests_per_minute=10_000, http_burst=128, http_concurrency=128))
        action = ActionSpec(
            id="act_preflight", task_id=task.id, solver_id="solver",
            kind="http", capability="http.request", target=base,
            arguments={"method": "POST", "path": "/", "body_format": "form", "body": {"a": "1"}, "assertions": {"parameter_count": 2}},
            rationale="validate form", risk="active",
        )
        result = executor.execute(task=task, action=action, workspace=tmp_path)
        assert result.status == "blocked"
        assert result.error and result.error.code == "EXECUTION_BACKEND_REQUIRED"
        assert NeverCalled.calls == 0
    finally:
        server.shutdown(); server.server_close()


def test_http_cookie_profiles_are_isolated_by_task_solver_and_origin(tmp_path):
    class CookieHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = (self.headers.get("Cookie") or "missing").encode()
            self.send_response(200)
            if self.path == "/set":
                self.send_header("Set-Cookie", "sid=isolated; Path=/; HttpOnly")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *_): return

    first = ThreadingHTTPServer(("127.0.0.1", 0), CookieHandler)
    second = ThreadingHTTPServer(("127.0.0.1", 0), CookieHandler)
    threading.Thread(target=first.serve_forever, daemon=True).start()
    threading.Thread(target=second.serve_forever, daemon=True).start()
    try:
        from tga.capabilities.http import execute_http
        from tga.capabilities.http_session import HTTPSessionRegistry
        from tga.capabilities.schemas import HTTPRequestArguments
        from tga.contracts import ActionSpec

        base = f"http://127.0.0.1:{first.server_port}"
        other = f"http://127.0.0.1:{second.server_port}"
        sessions = HTTPSessionRegistry()

        def request(task_id: str, solver_id: str, target: str, path: str) -> str:
            task = TGATask(id=task_id, name=task_id, mode="ctf", task_entry_url=f"{target}/", goal="test", execution_policy=execution_policy([base, other]))
            action = ActionSpec(id=f"act_{task_id}_{solver_id}_{path[-3:]}", task_id=task_id, solver_id=solver_id, kind="http", capability="http.request", target=target, arguments={"method": "GET", "path": path}, rationale="cookie isolation test", risk="passive")
            _, raw, _, _ = execute_http(task=task, action=action, args=HTTPRequestArguments(method="GET", path=path), sessions=sessions)
            return raw.decode()

        request("task_a", "solver_a", base, "/set")
        assert "sid=isolated" in request("task_a", "solver_a", base, "/check")
        assert request("task_a", "solver_b", base, "/check") == "missing"
        assert request("task_b", "solver_a", base, "/check") == "missing"
        assert request("task_a", "solver_a", other, "/check") == "missing"
        assert sessions.destroy(task_id="task_a", solver_id="solver_a") == 2
        assert request("task_a", "solver_a", base, "/check") == "missing"
    finally:
        first.shutdown(); first.server_close(); second.shutdown(); second.server_close()
