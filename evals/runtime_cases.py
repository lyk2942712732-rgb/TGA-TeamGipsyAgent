"""Executable local evaluation cases for the durable v2 runtime.

Each case drives the real Manager and controlled capability executor.  The web
cases use an in-process HTTP target, while the local case uses the constrained
workspace capability; no model self-assessment or prerecorded snapshot is used.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from tga.capabilities.runtime import ControlledActionExecutor
from tga.contracts import ActionResult, ActionSpec, Hypothesis, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.runtime.board import HypothesisDraft
from tga.runtime.manager import Manager


class _TargetHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/hidden":
            body = b"hidden route proof flag{eval_hidden_route}"
        else:
            body = b"<form method='POST' action='/login'><input name='username'><input name='password'></form>"
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0")); body = self.rfile.read(size).decode("utf-8", errors="replace")
        if self.path == "/login" and "username=admin" in body and "password=proof" in body:
            payload = b"form proof flag{eval_form_post}"
        else:
            payload = b"invalid form"
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(payload)

    def log_message(self, *_: object) -> None:
        return


@dataclass
class _Server:
    server: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def target(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown(); self.thread.join(timeout=2); self.server.server_close()


def _server() -> _Server:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    return _Server(server, thread)


class CaseSolver:
    model_name = "eval-case-solver"

    def __init__(self, *, action_factory, attack_class: str) -> None:
        self.action_factory = action_factory; self.attack_class = attack_class

    def initial_hypotheses(self, *, task: TGATask, solver_id: str) -> list[HypothesisDraft]:
        return [HypothesisDraft(statement=f"Evaluation case can prove {self.attack_class} through one bounded action.", attack_class=self.attack_class, entry_point=task.target, rationale="Local deterministic test target.", next_test="Execute the case action and retain its artifact.", confidence=0.9)]

    def propose_action(self, *, task: TGATask, solver_id: str, hypothesis: Hypothesis, snapshot: dict) -> ActionSpec | None:
        return self.action_factory(task, solver_id, hypothesis.id)

    def result_summary(self, *, hypothesis: Hypothesis, result: ActionResult) -> str:
        return result.summary


def _action(*, task: TGATask, solver_id: str, hypothesis_id: str, capability: str, kind: str, target: str, arguments: dict, risk: str = "passive") -> ActionSpec:
    return ActionSpec(id=f"eval_{uuid4().hex[:12]}", task_id=task.id, solver_id=solver_id, hypothesis_id=hypothesis_id, capability=capability, kind=kind, target=target, arguments=arguments, rationale="Executable evaluation action.", risk=risk)  # type: ignore[arg-type]


def run_case(case_id: str, root: Path) -> tuple[dict, dict]:
    server = _server() if case_id in {"web-hidden-route", "web-form-post", "failed-pivot"} else None
    try:
        target = server.target if server else "workspace://eval"
        scope = [f"127.0.0.1:{server.server.server_port}"] if server else ["workspace"]
        task = TGATask(id=f"eval_{case_id.replace('-', '_')}", name=case_id, mode="binary_ctf" if case_id == "local-code-binary" else "ctf", target=target, scope=scope, allow_active_scan=True, goal="Run the deterministic runtime evaluation.", flag_format=r"flag\{[^}]+\}")
        task_root = root / task.id; store = EvidenceStore(task_root / "evidence.db"); store.create_task(task)
        artifacts = ArtifactStore(task_root / "artifacts")
        if case_id == "web-hidden-route":
            factory = lambda task, solver, hyp: _action(task=task, solver_id=solver, hypothesis_id=hyp, capability="http.request", kind="http", target=task.target, arguments={"method": "GET", "path": "/hidden"})
            expected = {"flag": "flag{eval_hidden_route}", "status": "completed", "post": False}
        elif case_id == "web-form-post":
            factory = lambda task, solver, hyp: _action(task=task, solver_id=solver, hypothesis_id=hyp, capability="http.request", kind="http", target=task.target, arguments={"method": "POST", "path": "/login", "headers": {"Content-Type": "application/x-www-form-urlencoded"}, "body": {"username": "admin", "password": "proof"}}, risk="active")
            expected = {"flag": "flag{eval_form_post}", "status": "completed", "post": True}
        elif case_id == "failed-pivot":
            factory = lambda task, solver, hyp: _action(task=task, solver_id=solver, hypothesis_id=hyp, capability="http.request", kind="http", target="http://127.0.0.1:1", arguments={"method": "GET", "path": "/outside"})
            expected = {"flag": None, "status": "blocked", "post": False, "scope_rejected": True}
        elif case_id == "local-code-binary":
            factory = lambda task, solver, hyp: _action(task=task, solver_id=solver, hypothesis_id=hyp, capability="workspace.python", kind="workspace", target="solver workspace", arguments={"source": "print('flag{eval_local_binary}')", "argv": []}, risk="active")
            expected = {"flag": "flag{eval_local_binary}", "status": "completed", "post": False}
        else:
            raise ValueError(f"unknown evaluation case: {case_id}")
        manager = Manager(store=store, run_root=root, executor=ControlledActionExecutor(artifact_store=artifacts), solver=CaseSolver(action_factory=factory, attack_class="workspace" if case_id == "local-code-binary" else "web"))
        snapshot = manager.run_session(task.id)
        store.close()
        return snapshot, expected
    finally:
        if server:
            server.close()
