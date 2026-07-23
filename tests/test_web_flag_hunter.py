from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tga.contracts import Intent, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.orchestrator.scheduler import Scheduler
from tga.workers.subprocess_worker import SubprocessWorker


class FlagHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/flag":
            body = b"flag{web_hunter_real_output}"
        else:
            body = b"<a href='/flag'>flag</a>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return


def test_web_flag_hunter_confirms_flag_from_real_http_output(tmp_path: Path, monkeypatch):
    # This fixture verifies the deterministic local hunter, not an operator's
    # ambient configured provider. Keep it isolated from settings-route tests.
    monkeypatch.delenv("TGA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TGA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TGA_LLM_MODEL", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), FlagHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store: EvidenceStore | None = None
    try:
        target = f"http://127.0.0.1:{server.server_port}"
        task = TGATask(
            id="task_web_flag",
            name="web-flag",
            mode="ctf",
            target=target,
            scope=[f"127.0.0.1:{server.server_port}"],
            intensity="normal",
            allow_active_scan=True,
            goal="get flag",
            flag_format=r"flag\{[^}]+\}",
        )
        intent = Intent(
            id="intent_exploit",
            task_id=task.id,
            kind="exploit_ctf",
            target=target,
            goal="recover flag",
            risk="active",
        )
        run_root = tmp_path / "runs"
        artifact_store = ArtifactStore(run_root / task.id / "artifacts")
        store = EvidenceStore(run_root / task.id / "evidence.db")
        store.create_task(task)
        worker = SubprocessWorker(artifact_store=artifact_store, timeout_s=3)

        Scheduler(store=store, worker=worker, run_root=str(run_root)).run_intent(task=task, intent=intent)

        snapshot = store.task_snapshot(task.id)
        assert snapshot["flags"][0]["value"] == "flag{web_hunter_real_output}"
    finally:
        if store is not None:
            store.close()
        server.shutdown()
        server.server_close()
