from __future__ import annotations

import contextlib
import http.server
import socketserver
import threading
import time

from tga.capabilities.executor import CapabilityExecutor
from tga.capabilities.models import ActionSpec


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/flag":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html>flag{from_http}</html>")
            return
        if self.path == "/big":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"A" * 5000)
            return
        if self.path == "/slow":
            time.sleep(2)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"slow")
            return
        if self.path == "/redirect-out":
            self.send_response(302)
            self.send_header("Location", "http://example.com/flag")
            self.end_headers()
            return
        if self.path == "/download":
            self.send_response(200)
            self.send_header("Content-Disposition", "attachment; filename=x.bin")
            self.end_headers()
            self.wfile.write(b"bin")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):
        return


class Server(socketserver.TCPServer):
    allow_reuse_address = True


@contextlib.contextmanager
def local_server():
    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def spec(url: str, **arguments) -> ActionSpec:
    return ActionSpec(
        task_id="task_http",
        solver_id="solver_a",
        action_id="action_http",
        capability="http.request",
        target=url,
        scope=[url.rsplit("/", 1)[0]],
        flag_format=r"flag\{[^}]+\}",
        arguments={"url": url, **arguments},
    )


def test_http_request_success_and_candidate_flag(tmp_path):
    with local_server() as base:
        result = CapabilityExecutor(run_root=tmp_path).execute(spec(f"{base}/flag"))

    assert result.status == "ok"
    assert result.candidate_flags == ["flag{from_http}"]
    assert result.artifacts


def test_http_request_blocks_out_of_scope_redirect(tmp_path):
    with local_server() as base:
        result = CapabilityExecutor(run_root=tmp_path).execute(spec(f"{base}/redirect-out", allow_redirects=True))

    assert result.status == "blocked"
    assert result.error and result.error.code == "OUT_OF_SCOPE_REDIRECT"
    assert result.artifacts


def test_http_request_blocks_download(tmp_path):
    with local_server() as base:
        result = CapabilityExecutor(run_root=tmp_path).execute(spec(f"{base}/download"))

    assert result.status == "blocked"
    assert result.error and result.error.code == "DOWNLOAD_NOT_ALLOWED"


def test_http_request_timeout_and_truncation(tmp_path):
    with local_server() as base:
        truncated = CapabilityExecutor(run_root=tmp_path).execute(spec(f"{base}/big", max_output_bytes=1024))
        timed_out = CapabilityExecutor(run_root=tmp_path).execute(
            spec(f"{base}/slow", timeout_seconds=1),
        )

    assert truncated.status == "ok"
    assert truncated.output_truncated
    assert timed_out.status in {"failed", "timeout"}
    assert timed_out.artifacts
