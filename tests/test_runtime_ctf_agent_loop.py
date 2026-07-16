from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from tga.capabilities.runtime import ControlledActionExecutor
from tga.contracts import TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.models.base import ModelResponse
from tga.runtime.manager import Manager
from tga.runtime.solver import LLMRuntimeSolver, MainSolver


class _ChallengeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"<a href='/puzzle'>continue</a>" if self.path == "/" else b"CTF{artifact_backed_agent_loop}"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        return


class _PhpExecutorHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<form method='POST'><textarea name='code'></textarea></form>")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        values = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        body = b"CTF{form_action_evidence_loop}" if values.get("code") else b"missing code"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        return


class _ExpiredChallengeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(404)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write("<h1>404 容器已过期或未创建完成</h1>".encode())

    def log_message(self, *_: object) -> None:
        return


class _EvidenceDrivenModel:
    model = "evidence-driven-test-model"

    def __init__(self) -> None:
        self.messages = []

    def chat(self, messages, *, temperature=0.2):
        self.messages.append(messages)
        context = json.loads(messages[-1].content).get("context", {})
        observations = context.get("artifact_observations") or []
        path = "/puzzle" if observations else "/"
        return ModelResponse(
            content=json.dumps({"action": {"capability": "http.request", "arguments": {"method": "GET", "path": path}, "rationale": f"persisted evidence requires {path}"}}),
            model=self.model,
            raw={},
        )


class _FormEvidenceDrivenModel:
    model = "form-evidence-driven-test-model"

    def __init__(self) -> None:
        self.messages = []

    def chat(self, messages, *, temperature=0.2):
        self.messages.append(messages)
        context = json.loads(messages[-1].content).get("context", {})
        observations = context.get("artifact_observations") or []
        action = (
            {"capability": "http.request", "arguments": {"method": "GET", "path": "/"}, "rationale": "establish the authorized landing contract"}
            if not observations
            else {
                "capability": "http.request",
                "arguments": {
                    "method": "POST", "path": "/", "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                    "body": {"code": "echo 'authorized CTF proof';"},
                },
                "rationale": "the persisted landing artifact exposes the POST code form",
            }
        )
        return ModelResponse(content=json.dumps({"action": action}), model=self.model, raw={})


def test_llm_tool_feedback_reaches_next_turn_and_confirms_artifact_flag(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChallengeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store: EvidenceStore | None = None
    try:
        target = f"http://127.0.0.1:{server.server_port}"
        task = TGATask(
            id="agent_loop", name="agent loop", mode="ctf", target=target,
            scope=[target], goal="recover the authorized CTF flag",
            flag_format=r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}",
        )
        root = tmp_path / "runs"
        store = EvidenceStore(root / task.id / "evidence.db")
        store.create_task(task)
        model = _EvidenceDrivenModel()
        manager = Manager(
            store=store, run_root=root, solver=LLMRuntimeSolver(model),
            executor=ControlledActionExecutor(artifact_store=ArtifactStore(root / task.id / "artifacts")),
        )

        snapshot = manager.run_session(task.id)

        assert snapshot["session"]["status"] == "completed"
        assert snapshot["flags"][0]["value"] == "CTF{artifact_backed_agent_loop}"
        assert [item["arguments"]["path"] for item in snapshot["actions"]] == ["/", "/puzzle"]
        assert len(model.messages) == 2
        second_context = json.loads(model.messages[1][-1].content)["context"]
        http_observation = next(item for item in second_context["artifact_observations"] if "http" in item)
        assert http_observation["http"]["page"]["links"] == [f"{target}/puzzle"]
    finally:
        if store is not None:
            store.close()
        server.shutdown()
        server.server_close()


def test_fallback_solver_follows_one_persisted_in_scope_link_and_confirms_flag(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChallengeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store: EvidenceStore | None = None
    try:
        target = f"http://127.0.0.1:{server.server_port}"
        task = TGATask(
            id="fallback_loop", name="fallback loop", mode="ctf", target=target,
            scope=[target], goal="recover the authorized CTF flag",
            flag_format=r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}",
        )
        root = tmp_path / "runs"
        store = EvidenceStore(root / task.id / "evidence.db")
        store.create_task(task)
        snapshot = Manager(
            store=store, run_root=root, solver=MainSolver(),
            executor=ControlledActionExecutor(artifact_store=ArtifactStore(root / task.id / "artifacts")),
        ).run_session(task.id)

        assert snapshot["session"]["status"] == "completed"
        assert snapshot["flags"][0]["value"] == "CTF{artifact_backed_agent_loop}"
        assert [item["arguments"].get("path") or item["arguments"].get("url") for item in snapshot["actions"]] == ["/", f"{target}/puzzle"]
    finally:
        if store is not None:
            store.close()
        server.shutdown()
        server.server_close()


def test_llm_can_submit_an_observed_authorized_ctf_form_and_confirm_artifact_flag(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PhpExecutorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store: EvidenceStore | None = None
    try:
        target = f"http://127.0.0.1:{server.server_port}"
        task = TGATask(
            id="form_loop", name="form loop", mode="ctf", target=target, scope=[target],
            goal="recover the authorized CTF flag", allow_active_scan=True,
            flag_format=r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}",
        )
        root = tmp_path / "runs"
        store = EvidenceStore(root / task.id / "evidence.db")
        store.create_task(task)
        model = _FormEvidenceDrivenModel()
        snapshot = Manager(
            store=store, run_root=root, solver=LLMRuntimeSolver(model),
            executor=ControlledActionExecutor(artifact_store=ArtifactStore(root / task.id / "artifacts")),
        ).run_session(task.id)

        assert snapshot["session"]["status"] == "completed"
        assert snapshot["flags"][0]["value"] == "CTF{form_action_evidence_loop}"
        assert [item["arguments"]["method"] for item in snapshot["actions"]] == ["GET", "POST"]
        assert snapshot["actions"][1]["risk"] == "active"
    finally:
        if store is not None:
            store.close()
        server.shutdown()
        server.server_close()


def test_expired_hosted_ctf_container_is_recorded_as_challenge_expired(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ExpiredChallengeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store: EvidenceStore | None = None
    try:
        target = f"http://127.0.0.1:{server.server_port}"
        task = TGATask(
            id="expired_loop", name="expired loop", mode="ctf", target=target, scope=[target],
            goal="recover the authorized CTF flag", flag_format=r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}",
        )
        root = tmp_path / "runs"
        store = EvidenceStore(root / task.id / "evidence.db")
        store.create_task(task)
        snapshot = Manager(
            store=store, run_root=root, solver=MainSolver(),
            executor=ControlledActionExecutor(artifact_store=ArtifactStore(root / task.id / "artifacts")),
        ).run_session(task.id)

        assert snapshot["session"]["status"] == "blocked"
        assert snapshot["session"]["stop_reason"] == "challenge_expired"
        assert snapshot["challenge"]["status"] == "expired"
        assert snapshot["actions"][0]["result"]["error"]["code"] == "CHALLENGE_UNAVAILABLE"
    finally:
        if store is not None:
            store.close()
        server.shutdown()
        server.server_close()
