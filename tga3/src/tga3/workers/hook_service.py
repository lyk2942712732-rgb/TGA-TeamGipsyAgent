"""Resident hook handler with one pooled remote HTTP client per Worker process."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from .action_hook import HookActionRecorder
from .blackboard_hook import checkpoint


class HookService:
    def __init__(self):
        self.client = httpx.Client(timeout=10, trust_env=False, limits=httpx.Limits(keepalive_expiry=120))
        self.actions = HookActionRecorder()
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    event = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    result = checkpoint(event, client=service.client, record_action=service.actions.record)
                    status = 200
                except Exception as exc:
                    result, status = {'error': f'{type(exc).__name__}: {exc}'}, 500
                body = json.dumps(result, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.client.close()
