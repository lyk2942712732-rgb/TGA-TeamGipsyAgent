"""Run an opt-in, real-Provider ReAct smoke test against a local CTF fixture.

This script deliberately prints only status, event types, and Artifact counts.
It never prints provider messages, HTTP bodies, or credentials.  It is not a
unit test: run it only after explicitly verifying the configured Provider.
"""

from __future__ import annotations

import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

from tga.contracts import (
    CtfModeConfig,
    ExecutionPolicy,
    HighImpactExecutionPolicy,
    LocalComputeExecutionPolicy,
    ModelSnapshot,
    NetworkExecutionPolicy,
    TGATask,
)
from tga.models.bootstrap import model_config_status
from tga.runtime.manager import Manager
from tga.runtime.service import TaskRuntimeService


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"CTF training fixture. The verified flag is TGA{real_provider_http_smoke}."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, help="Empty disposable directory for this smoke run")
    parser.add_argument("--max-turns", type=int, default=8)
    args = parser.parse_args()
    run_root = Path(args.run_root).resolve()
    if run_root.exists() and any(run_root.iterdir()):
        raise SystemExit("--run-root must be empty to preserve smoke-test isolation")
    run_root.mkdir(parents=True, exist_ok=True)

    status = model_config_status()
    verification = status.get("verification") or {}
    if status.get("verification_status") != "verified":
        raise SystemExit("configured Provider must be verified before real ReAct smoke testing")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        task_id = f"real_provider_{uuid4().hex[:12]}"
        os.environ["TGA_MAX_SESSION_TURNS"] = str(max(2, min(args.max_turns, 16)))
        task = TGATask(
            id=task_id,
            name="Real Provider HTTP smoke",
            mode="ctf",
            session_input={
                "prompt": (
                    f"The authorized CTF fixture is {origin}/. Use http.request to read it, "
                    "cite the returned Artifact, then call finish_session with the flag exactly as observed."
                ),
                "files": [],
            },
            task_entry_url=f"{origin}/",
            goal="Read the local CTF fixture and submit its Artifact-backed flag.",
            mode_config=CtfModeConfig(flag_format=r"TGA\{[^{}\s]+\}"),
            execution_policy=ExecutionPolicy(
                preset="custom",
                network=NetworkExecutionPolicy(
                    access="task_sources",
                    interaction="observe",
                    seed_origins=[origin],
                    deny_private_networks=False,
                    deny_loopback=False,
                    deny_link_local=True,
                    deny_cloud_metadata=True,
                    rate_limit_per_minute=10,
                    concurrency=1,
                    request_timeout_seconds=15,
                ),
                local_compute=LocalComputeExecutionPolicy(mode="disabled"),
                high_impact=HighImpactExecutionPolicy(mode="forbidden"),
            ),
            model_snapshot=ModelSnapshot(
                provider=str(status.get("provider") or "openai-compatible"),
                model=str(status.get("model") or ""),
                capability_fingerprint=str(verification.get("capability_fingerprint") or ""),
                verification_id=str(verification.get("id") or ""),
                verified_at=str(verification.get("verified_at") or ""),
                capabilities=dict(verification.get("capabilities") or {}),
                max_output_tokens=int(status.get("max_output_tokens") or 1024),
                timeout_seconds=int(status.get("timeout_seconds") or 60),
                temperature=float(status.get("temperature") or 0.2),
                reasoning_mode=str(status.get("reasoning_mode") or "auto"),
            ),
        )
        manager = Manager(run_root=run_root)
        service = TaskRuntimeService(run_root=run_root, manager=manager)
        service.create_task(task)
        snapshot = service.run_task(task_id)
        events = snapshot.get("agent_events") or []
        event_types = [item.get("type") for item in events]
        actions = snapshot.get("actions") or []
        artifacts = snapshot.get("artifacts") or []
        session = snapshot.get("session") or {}
        result = {
            "status": session.get("status"),
            "turn_count": session.get("turn_count"),
            "http_action_succeeded": any(
                item.get("capability") == "http.request" and item.get("status") == "succeeded"
                for item in actions
            ),
            "artifact_count": len(artifacts),
            "event_types": [
                name for name in ("SESSION_STARTED", "MANAGER_DECISION", "TOOL_EXECUTION_START", "TOOL_EXECUTION_END", "FINISH_ACCEPTED", "SESSION_STOPPED")
                if name in event_types
            ],
            "flag_count": len(snapshot.get("flags") or []),
        }
        print(result)
        required_events = {"SESSION_STARTED", "MANAGER_DECISION", "TOOL_EXECUTION_START", "TOOL_EXECUTION_END", "FINISH_ACCEPTED", "SESSION_STOPPED"}
        return 0 if (
            result["status"] == "completed"
            and result["http_action_succeeded"]
            and result["flag_count"] == 1
            and required_events.issubset(set(result["event_types"]))
        ) else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
