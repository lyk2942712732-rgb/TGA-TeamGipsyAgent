import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tga3.workers.blackboard_hook import checkpoint
from tga3.workers.cli_adapter import ClaudeAdapter, CodexAdapter
from tga3.workers.runtime import WorkerSession


def environment(monkeypatch, root, *, claude=False):
    ident = str(uuid4())
    for key, value in {
        "TGA3_MODEL_NAME": "fixture",
        "TGA3_API_KEY": "fixture-secret",
        "TGA3_SYSTEM_PROMPT": "Solve this question.",
        "TGA3_PROVIDER_PROTOCOL": "anthropic" if claude else "openai_responses",
        "TGA3_TASK_ID": ident,
        "TGA3_QUESTION_ID": ident,
        "TGA3_AOE_PARENT_ID": str(uuid4()),
        "TGA3_AGENT_ID": "worker-claude" if claude else "worker-openai",
        "TGA3_AGENT_ROLE": "worker",
        "TGA3_AGENT_DISPLAY_NAME": "Worker",
        "TGA3_AGENT_RUNTIME": "claude_cli" if claude else "codex_cli",
        "TGA3_WORKSPACE": str(root),
        "TGA3_LOGS": str(root / "logs"),
        "TGA3_BLACKBOARD_MCP_URL": "http://fixture/mcp/",
        "TGA3_CONTROL_WS_URL": "ws://fixture/ws",
        "TGA3_STARTUP_PROMPT": "Start",
        "TGA3_PERIODIC_PROMPT": "Continue",
        "TGA3_CLI_GENERATION": "generation",
    }.items():
        monkeypatch.setenv(key, value)
    return ident


@pytest.mark.parametrize("adapter_type", [CodexAdapter, ClaudeAdapter])
def test_fresh_aoe_attempt_ignores_pointer_but_joint_can_resume_its_own_question(monkeypatch, tmp_path, adapter_type):
    ident = environment(monkeypatch, tmp_path, claude=adapter_type is ClaudeAdapter)
    original = adapter_type()
    original.remember_session("own-question-native-session")
    saved = original.pointer.read_bytes()
    monkeypatch.setenv("TGA3_FORCE_NEW_SESSION", "1")
    fresh = adapter_type()
    assert fresh.session_id is None
    assert "resume" not in fresh.command()[0] and "--resume" not in fresh.command()[0]
    assert original.pointer.read_bytes() == saved
    monkeypatch.setenv("TGA3_FORCE_NEW_SESSION", "0")
    resumed = adapter_type()
    assert resumed.session_id == "own-question-native-session"
    assert "own-question-native-session" in resumed.command()[0]
    assert json.loads(saved)["question_id"] == ident


def test_takeover_hook_forces_board_before_first_execution_and_does_not_log_denied_tool(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path)
    monkeypatch.setenv("TGA3_BOARD_START_TOKEN", "new-attempt-token")
    calls, records = [], []

    def post(_url, *, json, **_kwargs):
        calls.append(json)
        value = {"delivery_id": "delivery", "context": "Peer's latest verified finding"} if len(calls) == 1 else {}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: value)

    monkeypatch.setattr("tga3.workers.blackboard_hook.httpx.post", post)
    monkeypatch.setattr("tga3.workers.blackboard_hook.record_tool", lambda event: records.append(event))
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "probe"}}
    denied = checkpoint(event)["hookSpecificOutput"]
    assert denied["permissionDecision"] == "deny" and "Peer's latest" in denied["additionalContext"]
    assert calls[0]["final"] and not calls[0]["initial_only"] and not records
    assert checkpoint(event) == {}
    assert len(records) == 1 and calls[1]["ack"] == "delivery"


async def test_worker_deadline_cancels_active_cycle_without_host_control(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path)
    monkeypatch.setenv("TGA3_ATTEMPT_DEADLINE", str(time.time() + 0.08))

    class Adapter:
        cancelled = False

        async def run_cycle(self, *_args):
            try:
                await asyncio.sleep(30)
            finally:
                self.cancelled = True

    class Socket:
        async def send(self, _payload):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(30)
            raise StopAsyncIteration

    @asynccontextmanager
    async def connect(*_args, **_kwargs):
        yield Socket()

    monkeypatch.setattr("websockets.asyncio.client.connect", connect)
    adapter = Adapter()
    worker = WorkerSession(adapter)
    async with asyncio.timeout(1):
        await worker.run()
    assert worker.stop.is_set() and adapter.cancelled
