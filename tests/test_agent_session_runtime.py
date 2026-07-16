import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tga.capabilities.runtime import ControlledActionExecutor, ExecutionBudget
from tga.contracts import ActionResult, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.runtime.manager import Manager


class ToolLoopModel:
    model = "agent-test-model"

    def __init__(self):
        self.calls = []

    def chat_tools(self, messages, *, tools, temperature=0.2):
        self.calls.append({"messages": messages, "tools": tools})
        return {
            "message": {
                "role": "assistant",
                "content": "I will inspect the target.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "tga_http_request",
                            "arguments": json.dumps({"method": "GET", "path": "/"}),
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }


class FlagToolExecutor:
    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts
        self.tasks = []

    def execute(self, *, task, action, workspace):
        self.tasks.append(task)
        artifact = self.artifacts.save_text(
            task_id=task.id,
            intent_id=action.id,
            kind="http_response",
            text='{"body":"CTF{agent_session_works}"}',
            tool=action.capability,
            target=task.target,
            suffix=".json",
        )
        return ActionResult(
            action_id=action.id,
            task_id=task.id,
            solver_id=action.solver_id,
            status="succeeded",
            summary="target returned a flag",
            artifact_ids=[artifact.id],
            candidate_flags=["CTF{agent_session_works}"],
        )


class _FlagHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html><body>CTF{native_http_tool_loop}</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return


def test_product_runtime_uses_one_native_agent_tool_session(tmp_path, monkeypatch):
    run_root = tmp_path / "runs"
    task = TGATask(
        id="agent_session",
        name="Agent Session",
        mode="ctf",
        target="https://challenge.example",
        goal="solve the challenge",
        flag_format=r"CTF\{[^}]+\}",
    )
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    model = ToolLoopModel()
    executor = FlagToolExecutor(ArtifactStore(run_root / task.id / "artifacts"))
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)

    snapshot = Manager(store=store, run_root=run_root, executor=executor).run_session(task.id)

    assert snapshot["session"]["status"] == "completed"
    assert snapshot["flags"][0]["value"] == "CTF{agent_session_works}"
    assert len(snapshot["solvers"]) == 1
    assert snapshot["board"]["hypotheses"] == []
    assert [event["type"] for event in snapshot["agent_events"] if event["type"].startswith("TOOL_")] == [
        "TOOL_EXECUTION_START",
        "TOOL_EXECUTION_END",
    ]
    assert executor.tasks[0].allow_active_scan is True
    assert executor.tasks[0].insecure_tls_origins == ["https://challenge.example"]
    transcript = json.loads(
        (run_root / task.id / "solvers" / snapshot["solvers"][0]["id"] / "session" / "messages.json").read_text(encoding="utf-8")
    )
    assert [message["role"] for message in transcript] == ["system", "user", "assistant", "tool"]
    store.close()


def test_task_target_derives_legacy_scope_automatically():
    task = TGATask(id="derived", name="derived", mode="web_audit", target="https://example.test/path", goal="audit")
    assert task.scope == ["https://example.test"]


def test_native_agent_session_executes_real_http_tool_without_scope_switches(tmp_path, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlagHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run_root = tmp_path / "runs"
        target = f"http://127.0.0.1:{server.server_port}"
        task = TGATask(
            id="native_http",
            name="native http",
            mode="ctf",
            target=target,
            goal="find the flag",
            flag_format=r"CTF\{[^}]+\}",
        )
        store = EvidenceStore(run_root / task.id / "evidence.db")
        store.create_task(task)
        model = ToolLoopModel()
        monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
        executor = ControlledActionExecutor(
            artifact_store=ArtifactStore(run_root / task.id / "artifacts"),
            budget=ExecutionBudget(unrestricted=True),
        )

        snapshot = Manager(store=store, run_root=run_root, executor=executor).run_session(task.id)

        assert snapshot["session"]["status"] == "completed"
        assert snapshot["flags"][0]["value"] == "CTF{native_http_tool_loop}"
        assert snapshot["actions"][0]["capability"] == "http.request"
        assert snapshot["artifacts"]
        store.close()
    finally:
        server.shutdown()
        server.server_close()
