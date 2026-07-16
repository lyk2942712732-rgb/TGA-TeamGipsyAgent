from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tga.models.base import ModelMessage
from tga.models.openai_compatible import OpenAICompatibleClient


class _ToolCallHandler(BaseHTTPRequestHandler):
    request_body: dict = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        type(self).request_body = json.loads(self.rfile.read(length).decode("utf-8"))
        response = {
            "choices": [{"message": {"tool_calls": [{"type": "function", "function": {
                "name": "propose_tga_action",
                "arguments": '{"action":{"capability":"http.request","arguments":{"method":"GET","path":"/"},"rationale":"observe"}}',
            }}]}}],
        }
        raw = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_: object) -> None:
        return


class _ThinkingToolCallHandler(_ToolCallHandler):
    request_bodies: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).request_bodies.append(body)
        if "tool_choice" in body:
            raw = json.dumps({"error": {"message": "Thinking mode does not support this tool_choice"}}).encode("utf-8")
            self.send_response(400)
        else:
            raw = json.dumps({
                "choices": [{"message": {"tool_calls": [{"type": "function", "function": {
                    "name": "propose_tga_action",
                    "arguments": '{"action":{"capability":"http.request","arguments":{"method":"GET","path":"/"},"rationale":"observe"}}',
                }}]}}],
            }).encode("utf-8")
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_openai_compatible_client_requests_and_parses_native_action_tool() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ToolCallHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OpenAICompatibleClient(
            base_url=f"http://127.0.0.1:{server.server_port}", api_key="test-key", model="test-model",
        )
        response = client.chat_action_tool(
            [ModelMessage(role="system", content="controlled solver"), ModelMessage(role="user", content="plan")],
            tool_name="propose_tga_action", tool_description="propose one action",
            parameters={"type": "object", "properties": {"action": {"type": "object"}}},
            thinking=False,
        )

        assert json.loads(response.content)["action"]["capability"] == "http.request"
        request = _ToolCallHandler.request_body
        assert request["tool_choice"]["function"]["name"] == "propose_tga_action"
        assert request["thinking"] == {"type": "disabled"}
        assert request["tools"][0]["function"]["parameters"]["properties"]["action"]["type"] == "object"
    finally:
        server.shutdown()
        server.server_close()


def test_thinking_model_retries_without_forced_tool_choice() -> None:
    _ThinkingToolCallHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ThinkingToolCallHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OpenAICompatibleClient(
            base_url=f"http://127.0.0.1:{server.server_port}", api_key="test-key", model="thinking-model",
        )
        response = client.chat_action_tool(
            [ModelMessage(role="user", content="plan")],
            tool_name="propose_tga_action", tool_description="propose one action",
            parameters={"type": "object", "properties": {"action": {"type": "object"}}},
        )

        assert json.loads(response.content)["action"]["capability"] == "http.request"
        assert len(_ThinkingToolCallHandler.request_bodies) == 2
        assert "tool_choice" in _ThinkingToolCallHandler.request_bodies[0]
        assert "tool_choice" not in _ThinkingToolCallHandler.request_bodies[1]
        assert _ThinkingToolCallHandler.request_bodies[1]["tools"] == _ThinkingToolCallHandler.request_bodies[0]["tools"]
    finally:
        server.shutdown()
        server.server_close()


def test_non_thinking_planner_never_downgrades_from_forced_tool_choice() -> None:
    _ThinkingToolCallHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ThinkingToolCallHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OpenAICompatibleClient(
            base_url=f"http://127.0.0.1:{server.server_port}", api_key="test-key", model="planner-model",
        )
        with pytest.raises(RuntimeError, match="does not support this tool_choice"):
            client.chat_action_tool(
                [ModelMessage(role="user", content="plan")],
                tool_name="propose_tga_action", tool_description="propose one action",
                parameters={"type": "object", "properties": {"action": {"type": "object"}}},
                thinking=False,
            )

        assert len(_ThinkingToolCallHandler.request_bodies) == 1
        assert _ThinkingToolCallHandler.request_bodies[0]["thinking"] == {"type": "disabled"}
        assert "tool_choice" in _ThinkingToolCallHandler.request_bodies[0]
    finally:
        server.shutdown()
        server.server_close()


def test_agent_tool_turn_preserves_tool_calls_and_repairs_lone_surrogate() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ToolCallHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OpenAICompatibleClient(
            base_url=f"http://127.0.0.1:{server.server_port}", api_key="test-key", model="test-model",
        )
        response = client.chat_tools(
            [{"role": "user", "content": "broken surrogate: \ud800"}],
            tools=[{"type": "function", "function": {"name": "test_tool", "description": "test", "parameters": {"type": "object"}}}],
        )

        assert response["message"]["tool_calls"][0]["function"]["name"] == "propose_tga_action"
        assert "\ud800" not in _ToolCallHandler.request_body["messages"][0]["content"]
        assert _ToolCallHandler.request_body["tool_choice"] == "auto"
    finally:
        server.shutdown()
        server.server_close()
