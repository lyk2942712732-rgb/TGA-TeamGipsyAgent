import json

import pytest

from tga3.action_logs import read_action_log
from tga3.workers.action_hook import interrupt_hook_actions, record_tool
from tga3.workers.blackboard_hook import checkpoint
from tga3.workers.cli_adapter import ClaudeAdapter, CodexAdapter


@pytest.fixture
def hook_env(monkeypatch, tmp_path):
    for name, value in {"TGA3_AGENT_ROLE": "worker", "TGA3_TASK_ID": "task-id",
                        "TGA3_AGENT_ID": "worker-openai", "TGA3_AGENT_RUNTIME": "codex_cli",
                        "TGA3_WORKSPACE": str(tmp_path), "TGA3_LOGS": str(tmp_path / "logs"),
                        "TGA3_CLI_GENERATION": "generation-1", "TGA3_API_KEY": "secret-key",
                        "TGA3_MODEL_NAME": "fixture", "TGA3_PROVIDER_PROTOCOL": "openai_responses"}.items():
        monkeypatch.setenv(name, value)
    return tmp_path


def tool_event(event="PreToolUse", **extra):
    return {"hook_event_name": event, "tool_use_id": "call_0", "tool_name": "Bash",
            "tool_input": {"command": "fixture > response.txt"}, "cwd": "/workspace", **extra}


def test_synchronous_hooks_supply_id_and_body_before_model_without_blackboard_read(hook_env, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("PostToolUse must not poll the blackboard")

    monkeypatch.setattr("tga3.workers.blackboard_hook.httpx.post", forbidden)
    record_tool(tool_event())
    (hook_env / "response.txt").write_text("403 secret-key")
    reply = checkpoint(tool_event("PostToolUse", tool_response=""))
    actions = read_action_log(hook_env / "logs")["actions"]
    assert len(actions) == 1
    action = actions[0]
    assert action["artifact"]["attachments"] == ["response.txt"]
    assert (hook_env / action["artifact"]["output_path"]).read_text() == ""
    assert action["action_id"] in reply["hookSpecificOutput"]["additionalContext"]
    assert "write one JSON file in /findings/" in reply["hookSpecificOutput"]["additionalContext"]
    assert not list((hook_env / ".tga3/action-hooks").glob("*.json"))


def test_native_id_reuse_does_not_merge_actions_after_cli_resume(hook_env, monkeypatch):
    for generation in ("first", "second"):
        monkeypatch.setenv("TGA3_CLI_GENERATION", generation)
        record_tool(tool_event())
        record_tool(tool_event("PostToolUse", tool_response=generation))
    actions = read_action_log(hook_env / "logs")["actions"]
    assert len({action["action_id"] for action in actions}) == 2
    assert [action["tool_call_id"] for action in actions] == ["call_0", "call_0"]




@pytest.mark.parametrize("response,event", [("Process exited with code 7\nOutput: failed", "PostToolUse"),
                                           ({"isError": True}, "PostToolUse"),
                                           (None, "PostToolUseFailure")])
def test_failed_tool_result_stays_a_failed_action(hook_env, response, event):
    record_tool(tool_event())
    action = record_tool(tool_event(event, tool_response=response, error="fixture failure"))
    assert action["status"] == "failed"
    if isinstance(response, str):
        assert action["exit_code"] == 7


def test_interrupted_hook_action_keeps_partial_output(hook_env):
    record_tool(tool_event())
    (hook_env / "partial.txt").write_text("partial")
    interrupt_hook_actions("generation-1")
    action = read_action_log(hook_env / "logs")["actions"][0]
    assert action["status"] == "interrupted"
    assert action["artifact"]["attachments"] == ["partial.txt"]
    assert json.loads((hook_env / action["artifact"]["output_path"]).read_text())["result_received"] is False


@pytest.mark.parametrize("adapter_type", [CodexAdapter, ClaudeAdapter])
async def test_worker_stream_does_not_duplicate_hook_action(hook_env, adapter_type):
    async def emit(*args):
        pass

    record_tool(tool_event())
    record_tool(tool_event("PostToolUse", tool_response="verified"))
    adapter = adapter_type()
    if adapter_type is CodexAdapter:
        await adapter.event({"type": "item.completed", "item": {"type": "command_execution", "id": "item_0",
                             "command": "fixture > response.txt", "aggregated_output": "verified"}}, emit)
    else:
        await adapter.event({"type": "user", "message": {"content": [{"type": "tool_result",
                             "tool_use_id": "call_0", "content": "verified"}]}}, emit)
    assert len(read_action_log(hook_env / "logs")["actions"]) == 1
