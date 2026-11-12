"""Isolated no-network acceptance: actual four CLIs, synthetic model and platform.

Run inside the hosted image with this tests directory bind-mounted at /audit.
No real models, targets, credentials or historical challenge data are used.
"""

import asyncio
import json
import os
import re
import shlex
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tga3 import hosted

ROOT = Path("/audit/run-" + os.environ.get("SMOKE_RUN_ID", "default"))
BASE = "http://127.0.0.1:18869"
records = []
accepted = set()


def actions(workspace, agent):
    path = workspace.parents[1] / "logs" / agent / f"{agent}-actions.json"
    return json.loads(path.read_text())["actions"] if path.is_file() else []


class Provider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def output_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assert self.path == "/openapi/v1/challenges", self.path
        assert self.headers.get("BENCHMARK_TOKEN") == "synthetic-platform"
        self.output_json(
            [
                {
                    "unique_code": str(i),
                    "description": "Synthetic offline fixture: observe its marker.",
                    "difficulty": "easy",
                    "target_mode": "none",
                }
                for i in range(4)
            ]
        )

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/count_tokens"):
            return self.output_json({"input_tokens": 100})
        if self.path == "/openapi/v1/challenges/submit":
            assert self.headers.get("BENCHMARK_TOKEN") == "synthetic-platform"
            assert body["flag"] == "HTB{hosted_synthetic_fixture}"
            accepted.add(body["unique_code"])
            return self.output_json({"correct": True, "awarded": 100, "correct_flag_count": 1, "total_flag_count": 1})
        assert self.path.split("?", 1)[0] in {"/v1/responses", "/v1/chat/completions", "/v1/messages"}, self.path
        raw = json.dumps(body)
        if "opencode" in body.get("model", ""):
            Path("/audit/hosted-opencode-request.json").write_text(json.dumps(body, indent=2))
        tools = []
        namespace = {}
        for descriptor in body.get("tools", []):
            tools.append(descriptor.get("name") or descriptor.get("function", {}).get("name"))
            for nested in descriptor.get("tools", []):
                tools.append(nested.get("name"))
                namespace[nested.get("name")] = descriptor.get("name")
        assert not any("ctf42" in str(tool) for tool in tools)
        shell = next((tool for tool in tools if tool in {"bash", "Bash", "exec_command", "run_shell_command"}), None)
        collect = next((tool for tool in tools if tool and "scout_collect_questions" in tool), None)
        reporter_match = re.search(r"Read ([^\s\"\\]+)/report-input.json", raw)
        role = "scout" if collect else "reporter" if reporter_match else "worker"
        do_tool = bool(shell)
        tool = shell
        command = "echo synthetic"
        arguments = None
        tool_namespace = None
        workspace = None
        if collect:
            workspace = next((ROOT / "data/tasks").glob("*/workspaces/scout"))
            plan = {
                "request": {
                    "method": "GET",
                    "url": BASE + "/openapi/v1/challenges",
                    "headers": {"BENCHMARK_TOKEN": "synthetic-platform"},
                },
                "items_path": "",
                "fields": {
                    "source_key": "unique_code",
                    "title": "unique_code",
                    "description": "description",
                    "difficulty": "difficulty",
                },
                "target": {"mode": "none"},
                "submission": {
                    "request": {
                        "method": "POST",
                        "url": BASE + "/openapi/v1/challenges/submit",
                        "headers": {"BENCHMARK_TOKEN": "synthetic-platform"},
                        "body": {"unique_code": "{unique_code}", "flag": "{flag}"},
                    },
                    "success_path": "correct",
                    "duplicate_code": "duplicate",
                },
            }
            history = actions(workspace, "scout")
            manifest = workspace.parents[1] / "questions.json"
            if manifest.is_file() and json.loads(manifest.read_text()).get("collection_complete"):
                do_tool = False
            else:
                tool, arguments, tool_namespace = collect, {"plan": plan}, namespace.get(collect)
                for name in ["scene.md", "task.md"]:
                    read = "cat " + str(workspace / ".tga3/views/task-overview" / name)
                    if not any(a["status"] == "completed" and read in a["action"].get("command", "") for a in history):
                        tool, command, arguments, tool_namespace = shell, read, None, None
                        break
        elif reporter_match:
            workspace = Path(reporter_match[1])
            card = workspace / "experience-cards.json"
            do_tool = not card.is_file()
            command = "python3 -c " + shlex.quote(
                "from pathlib import Path;Path(" + repr(str(card)) + ").write_text('{\"cards\":[]}')"
            )
        elif shell:
            ident = re.search(r"- task_id: ([0-9a-f-]{36})", raw)
            agent = {
                "responses-model": "worker-openai",
                "messages-model": "worker-claude",
                "opencode-model": "worker-opencode",
                "qwen-model": "worker-qwen",
            }.get(body.get("model"))
            if not agent and body.get("model", "").startswith("tga/"):
                agent = "worker-opencode"
            if ident and agent:
                workspace = next((ROOT / "data/tasks").glob(f"*/{ident[1]}/workspaces/{agent}"))
                history = actions(workspace, agent)
                question = workspace.parents[1]
                source = json.loads((question / "source.json").read_text())
                unique_code = source.get("unique_code", source.get("item", {}).get("unique_code"))
                command = "python3 /audit/hosted_step.py"
                if (question / "final-candidate.json").is_file():
                    do_tool = False
                elif (question / "findings" / agent / "synthetic.json").is_file() and not any(
                    a["status"] == "completed" and "/challenges/submit" in a["action"].get("command", "")
                    for a in history
                ):
                    payload = json.dumps(
                        {"unique_code": unique_code, "flag": "HTB{hosted_synthetic_fixture}"}, separators=(",", ":")
                    )
                    command = (
                        f"curl -sS -X POST {BASE}/openapi/v1/challenges/submit "
                        "-H 'BENCHMARK_TOKEN: synthetic-platform' -H 'Content-Type: application/json' -d '"
                        + payload
                        + "'"
                    )
                for name in ["scene.md", "task.md"]:
                    read = "cat " + str(workspace / ".tga3/views/task-overview" / name)
                    if not any(a["status"] == "completed" and read in a["action"].get("command", "") for a in history):
                        command = read
                        break
            else:
                do_tool = False
        if not arguments:
            arguments = (
                {"cmd": command, "yield_time_ms": 10000, "max_output_tokens": 12000}
                if tool == "exec_command"
                else {"command": command, "timeout": 60000, "description": "Synthetic hosted acceptance"}
            )
        records.append(
            {
                "role": role,
                "model": body.get("model"),
                "tool": tool if do_tool else None,
                "workspace": str(workspace),
                "path": self.path,
                "shell": shell,
                "identity": bool(re.search(r"- task_id: ([0-9a-f-]{36})", raw)),
            }
        )
        self.respond(body, tool, arguments, do_tool, tool_namespace)

    def respond(self, body, tool, arguments, do_tool, namespace):
        call = "call_" + str(len(records))
        text = "# Synthetic hosted report\n\nEvidence was generated in this isolated test."
        if self.path.endswith("/responses"):
            item = (
                {
                    "type": "function_call",
                    "id": "fc_" + call,
                    "call_id": call,
                    "name": tool,
                    "arguments": json.dumps(arguments),
                    "status": "completed",
                }
                if do_tool
                else {
                    "type": "message",
                    "id": "msg_" + call,
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            )
            if namespace:
                item["namespace"] = namespace
            events = [
                {"type": "response.created", "response": {"id": "resp_" + call, "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0, "item": item},
            ]
            if do_tool:
                events.append(
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": item["id"],
                        "output_index": 0,
                        "arguments": json.dumps(arguments),
                    }
                )
            else:
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": item["id"],
                        "output_index": 0,
                        "content_index": 0,
                        "delta": text,
                    }
                )
            events += [
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_" + call,
                        "status": "completed",
                        "output": [item],
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    },
                },
            ]
        elif self.path.endswith("/chat/completions"):
            delta = (
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call,
                            "type": "function",
                            "function": {"name": tool, "arguments": json.dumps(arguments)},
                        }
                    ]
                }
                if do_tool
                else {"content": text}
            )
            packets = [
                {
                    "id": call,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                },
                {
                    "id": call,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    "id": call,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if do_tool else "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            ]
            return self.sse("".join("data: " + json.dumps(p) + "\n\n" for p in packets) + "data: [DONE]\n\n")
        else:
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_" + call,
                        "type": "message",
                        "role": "assistant",
                        "model": body.get("model"),
                        "content": [],
                        "stop_reason": None,
                        "usage": {"input_tokens": 1, "output_tokens": 0},
                    },
                },
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "tool_use", "id": call, "name": tool, "input": {}}
                    if do_tool
                    else {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(arguments)}
                    if do_tool
                    else {"type": "text_delta", "text": text},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use" if do_tool else "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
                {"type": "message_stop"},
            ]
        self.sse("".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events))

    def sse(self, content):
        content = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


async def main():
    env = {
        "CODEX_BASE_URL": "https://synthetic.maas.aliyuncs.com/v1",
        "CODEX_MODEL": "responses-model",
        "CODEX_API_KEY": "synthetic-key",
        "CLAUDE_BASE_URL": "https://synthetic.maas.aliyuncs.com",
        "CLAUDE_MODEL": "messages-model",
        "CLAUDE_API_KEY": "synthetic-key",
        "CHAT_BASE_URL": "https://synthetic.maas.aliyuncs.com/v1",
        "CHAT_MODEL": "chat-model",
        "CHAT_API_KEY": "synthetic-key",
        "OPENCODE_MODEL": "opencode-model",
        "QWEN_MODEL": "qwen-model",
        "BENCHMARK_BASE_URL": BASE,
        "BENCHMARK_TOKEN": "synthetic-platform",
    }
    os.environ.update(env)
    config = hosted.prepare_config(ROOT, Path("/opt/tga3-hosted/templates"))
    assert all(
        ".tsecbench.gw" in endpoint.base_url for p in config.models.providers for endpoint in p.endpoints.values()
    )
    # Test-only synthetic endpoint override. Production gateway conversion was
    # validated above and is never bypassed by the image's normal entry point.
    for provider in config.models.providers:
        for protocol, endpoint in provider.endpoints.items():
            endpoint.base_url = BASE if protocol == "anthropic" else BASE + "/v1"
    hosted.start_postgres(ROOT)
    original_create = hosted.create_app

    def checked_app(*args, **kwargs):
        app = original_create(*args, **kwargs)
        assert any("local-knowledge-mcp" in getattr(route, "path", "") for route in app.routes)
        return app

    hosted.create_app = checked_app
    provider = ThreadingHTTPServer(("127.0.0.1", 18869), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    prompt = (
        f"Synthetic offline acceptance only. BENCHMARK_TOKEN: synthetic-platform. All requests use that header.\n"
        f"Question-list API: GET {BASE}/openapi/v1/challenges\n"
        "Each item has unique_code, description, difficulty and target_mode=none. No target instance is required.\n"
        f"Submission API: POST {BASE}/openapi/v1/challenges/submit with JSON unique_code and flag. "
        "correct=true is success.\n"
    )
    try:
        result = await asyncio.wait_for(hosted.run(config, prompt), timeout=240)
        assert result == 0, result
        assert accepted == {"0", "1", "2", "3"}, accepted
        worker_models = {r["model"] for r in records if r["role"] == "worker" and r["tool"]}
        assert {"responses-model", "messages-model", "opencode-model", "qwen-model"} <= worker_models, worker_models
        reports = list((ROOT / "data/tasks").glob("*/*/writeups/*.md"))
        expected_reports = 4 if os.environ.get("PRODUCE_REPORT", "false").lower() == "true" else 0
        assert len(reports) == expected_reports, reports
        summary = {
            "passed": True,
            "network": "none",
            "real_cli_workers": sorted(worker_models),
            "submitted": len(accepted),
            "reports": len(reports),
            "model_calls": len(records),
            "knowledge_tools": 0,
            "runtime": "one container, no Docker socket",
        }
        Path("/audit/hosted-smoke-result.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
    finally:
        Path("/audit/hosted-model-calls.json").write_text(json.dumps(records, indent=2))
        provider.shutdown()
        provider.server_close()
        subprocess.run(
            [hosted.pg_binary("pg_ctl"), "-D", str(ROOT / "postgres"), "-m", "fast", "-w", "stop"], check=False
        )


if __name__ == "__main__":
    asyncio.run(main())
