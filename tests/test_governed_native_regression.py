from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tga.capabilities.runtime import ControlledActionExecutor, ExecutionBudget
from tga.contracts import TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.runtime.manager import Manager


FLAG = "CTF{governed_cookie_provenance}"


class RegressionHandler(BaseHTTPRequestHandler):
    request_count = 0

    def do_GET(self):  # noqa: N802
        type(self).request_count += 1
        if self.path != "/article":
            self.send_response(404); self.end_headers(); return
        shell = "navigation-shell " * 4000
        article = """
        <article><h1>Local challenge strategy</h1>
        <div><p>Verify this challenge version before active requests.</p></div>
        <p>POST the first request as an explicit form with three parameters.
        The success marker is `stage-ok` and the next request requires the same Cookie session.</p>
        <p>Then POST the login form and expect marker `welcome` before reading the flag.</p>
        </article>
        """
        body = f"<html><body><nav>{shell}</nav>{article}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        type(self).request_count += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        if self.path == "/prime" and body == "a=1&b=2&c=3":
            payload = b"stage-ok"
            self.send_response(200)
            self.send_header("Set-Cookie", "sid=secret-cookie-value; Path=/; HttpOnly")
        elif self.path == "/login" and "sid=secret-cookie-value" in self.headers.get("Cookie", "") and body == "user=admin&password=1":
            payload = f"welcome {FLAG}".encode()
            self.send_response(200)
        else:
            payload = b"session-or-form-invalid"
            self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers(); self.wfile.write(payload)

    def log_message(self, *_):
        return


class GovernedModel:
    model = "governed-regression-model"

    def __init__(self):
        self.calls: list[dict] = []

    def chat_tools(self, messages, *, tools, temperature=0.2):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": tools})
        index = len(self.calls)
        if index == 1:
            arguments = {"method": "GET", "path": "/article"}
        elif index == 2:
            arguments = {
                "method": "POST", "path": "/prime", "body_format": "form",
                "body": [["a", "1"], ["b", "2"], ["c", "3"]],
                "assertions": {"parameter_count": 3, "content_type": "application/x-www-form-urlencoded", "expected_marker": "stage-ok"},
            }
        elif index == 3:
            arguments = {
                "method": "POST", "path": "/login", "body_format": "form",
                "body": {"user": "admin", "password": "1"},
                "assertions": {"parameter_count": 2, "expected_marker": "welcome"},
            }
        else:
            result = json.loads(next(item["content"] for item in reversed(messages) if item["role"] == "tool"))
            return {
                "message": {"role": "assistant", "content": "Verified.", "tool_calls": [{
                    "id": "finish_4", "type": "function",
                    "function": {"name": "finish_session", "arguments": json.dumps({
                        "summary": "Recovered the flag through the governed request sequence.",
                        "evidence_artifact_ids": [item["artifact_id"] for item in result["artifacts"]],
                        "flag": FLAG,
                    })},
                }]},
                "finish_reason": "tool_calls",
            }
        return {
            "message": {"role": "assistant", "content": f"Execute governed step {index}.", "tool_calls": [{
                "id": f"call_{index}", "type": "function",
                "function": {"name": "tga_http_request", "arguments": json.dumps(arguments)},
            }]},
            "finish_reason": "tool_calls",
        }


def test_article_strategy_cookie_continuity_and_artifact_flag_gate(tmp_path, monkeypatch):
    RegressionHandler.request_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), RegressionHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        task = TGATask(
            id="governed_regression", name="governed regression", mode="ctf", target=base,
            scope=[base], goal="Use the supplied article and solve the local challenge.",
            flag_format=r"CTF\{[^}]+\}",
        )
        run_root = tmp_path / "runs"
        store = EvidenceStore(run_root / task.id / "evidence.db")
        store.create_task(task)
        model = GovernedModel()
        monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
        executor = ControlledActionExecutor(
            artifact_store=ArtifactStore(run_root / task.id / "artifacts"),
            budget=ExecutionBudget(unrestricted=True),
        )
        manager = Manager(store=store, run_root=run_root, executor=executor)
        manager.start_session(task_id=task.id, initial_hint=f"Read {base}/article first; its conclusions are candidates until verified.")

        snapshot = manager.run_session(task.id)

        assert snapshot["session"]["status"] == "completed"
        assert snapshot["flags"][0]["value"] == FLAG
        assert RegressionHandler.request_count == 3
        assert snapshot["board"]["strategy_cards"][0]["sources"][0]["extraction_status"] == "extracted"
        assert any(
            index["document_type"] == "html" and index["segments"] and "stage-ok" in index["summary"]
            for index in snapshot["artifact_indexes"]
        )
        raw_artifacts = [item for item in snapshot["artifacts"] if item["kind"] == "http_body"]
        article_artifact = next(item for item in raw_artifacts if item["target"].endswith("/article"))
        raw_article = (run_root / task.id / "artifacts" / article_artifact["path"]).read_text(encoding="utf-8")
        assert len(raw_article) > 30_000 and "Local challenge strategy" in raw_article
        second_context = json.dumps(model.calls[1]["messages"], ensure_ascii=False)
        assert "navigation-shell navigation-shell navigation-shell" not in second_context
        assert len(second_context) < 80_000
        session_events = [item for item in snapshot["agent_events"] if item["type"] == "HTTP_SESSION_STATUS"]
        assert any((item["payload"] or {}).get("reused") is True for item in session_events)
        serialized = json.dumps(snapshot, ensure_ascii=False)
        assert "secret-cookie-value" not in serialized
        assert any(item["type"] == "FLAG_CONFIRMED" for item in snapshot["agent_events"])
        assert all(action["strategy_step_id"] for action in snapshot["actions"])
        store.close()
    finally:
        server.shutdown(); server.server_close()


def test_form_preflight_rejects_before_network(tmp_path):
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
        task = TGATask(id="preflight", name="preflight", mode="ctf", target=base, scope=[base], goal="test")
        executor = ControlledActionExecutor(artifact_store=ArtifactStore(tmp_path / "artifacts"), budget=ExecutionBudget(unrestricted=True))
        action = ActionSpec(
            id="act_preflight", task_id=task.id, solver_id="solver", hypothesis_id="strategy",
            kind="http", capability="http.request", target=base,
            arguments={"method": "POST", "path": "/", "body_format": "form", "body": {"a": "1"}, "assertions": {"parameter_count": 2}},
            rationale="validate form", risk="active",
        )
        result = executor.execute(task=task, action=action, workspace=tmp_path)
        assert result.status == "blocked"
        assert result.error and result.error.code == "HTTP_EXECUTION_FAILED"
        assert "PARAMETER_COUNT_MISMATCH" in result.error.message
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
            task = TGATask(id=task_id, name=task_id, mode="ctf", target=target, scope=[base, other], goal="test")
            action = ActionSpec(id=f"act_{task_id}_{solver_id}_{path[-3:]}", task_id=task_id, solver_id=solver_id, hypothesis_id="strategy", kind="http", capability="http.request", target=target, arguments={"method": "GET", "path": path}, rationale="cookie isolation test", risk="passive")
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
