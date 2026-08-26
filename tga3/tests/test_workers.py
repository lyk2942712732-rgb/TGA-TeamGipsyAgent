import sys
from types import ModuleType

import pytest

from tga3.workers.claude_worker import ClaudeAdapter, ClaudeCliError
from tga3.workers.prompts import worker_instructions


@pytest.mark.asyncio
async def test_claude_worker_preserves_cli_stderr(monkeypatch):
    for name, value in {
        "TGA3_MODEL_NAME": "claude-test",
        "TGA3_API_KEY": "test-key",
        "TGA3_MAX_TURNS_PER_CYCLE": "3",
        "TGA3_BLACKBOARD_MCP_URL": "http://blackboard/mcp",
        "TGA3_SYSTEM_PROMPT": "Work independently.",
        "TGA3_TASK_ID": "task-id",
        "TGA3_AGENT_ID": "worker-claude",
        "TGA3_AGENT_DISPLAY_NAME": "Claude Worker",
        "TGA3_AGENT_RUNTIME": "claude_agent",
    }.items():
        monkeypatch.setenv(name, value)

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.stderr = kwargs["stderr"]

    class AssistantMessage:
        pass

    class ResultMessage:
        is_error = True
        result = "CLI process exited with code 1"
        subtype = "error_during_execution"
        errors = ["authentication failed"]
        session_id = "session"

    async def query(*, prompt, options):
        assert prompt == "continue"
        options.stderr("Error: invalid API key")
        yield ResultMessage()

    module = ModuleType("claude_agent_sdk")
    module.AssistantMessage = AssistantMessage
    module.ClaudeAgentOptions = ClaudeAgentOptions
    module.ResultMessage = ResultMessage
    module.TextBlock = type("TextBlock", (), {})
    module.ToolResultBlock = type("ToolResultBlock", (), {})
    module.ToolUseBlock = type("ToolUseBlock", (), {})
    module.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)

    async def emit(_method, _params):
        return None

    with pytest.raises(ClaudeCliError) as raised:
        await ClaudeAdapter().run_cycle("continue", emit)
    assert "CLI process exited with code 1" in str(raised.value)
    assert "authentication failed" in str(raised.value)
    assert "Error: invalid API key" in str(raised.value)


@pytest.mark.asyncio
async def test_claude_max_turns_is_a_resumable_cycle_boundary(monkeypatch):
    for name, value in {
        "TGA3_MODEL_NAME": "deepseek-v4-pro",
        "TGA3_API_KEY": "test-key",
        "TGA3_MAX_TURNS_PER_CYCLE": "3",
        "TGA3_BLACKBOARD_MCP_URL": "http://blackboard/mcp/",
        "TGA3_SYSTEM_PROMPT": "Work independently.",
        "TGA3_TASK_ID": "task-id",
        "TGA3_AGENT_ID": "worker-claude",
        "TGA3_AGENT_DISPLAY_NAME": "Claude Worker",
        "TGA3_AGENT_RUNTIME": "claude_agent",
    }.items():
        monkeypatch.setenv(name, value)

    class ClaudeAgentOptions:
        def __init__(self, **_kwargs):
            pass

    class ResultMessage:
        is_error = True
        result = None
        subtype = "error_max_turns"
        errors = []
        session_id = "resumable-session"

    async def query(**_kwargs):
        yield ResultMessage()

    module = ModuleType("claude_agent_sdk")
    module.AssistantMessage = type("AssistantMessage", (), {})
    module.ClaudeAgentOptions = ClaudeAgentOptions
    module.ResultMessage = ResultMessage
    module.TextBlock = type("TextBlock", (), {})
    module.ToolResultBlock = type("ToolResultBlock", (), {})
    module.ToolUseBlock = type("ToolUseBlock", (), {})
    module.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)

    async def emit(_method, _params):
        return None

    adapter = ClaudeAdapter()
    output = await adapter.run_cycle("continue", emit)
    assert "下一周期继续" in output
    assert adapter.session_id == "resumable-session"


def test_worker_prompt_injects_exact_blackboard_identity(monkeypatch):
    for name, value in {
        "TGA3_SYSTEM_PROMPT": "Work independently.",
        "TGA3_TASK_ID": "task-uuid",
        "TGA3_AGENT_ID": "worker-openai",
        "TGA3_AGENT_DISPLAY_NAME": "OpenAI Worker",
        "TGA3_AGENT_RUNTIME": "openai_agents",
        "TGA3_MODEL_NAME": "model-name",
    }.items():
        monkeypatch.setenv(name, value)

    prompt = worker_instructions()

    assert "task_id: task-uuid" in prompt
    assert '"agent_id":"worker-openai"' in prompt
    assert "Never invent a placeholder task ID or actor." in prompt
    assert "body={claim, detail?}; artifact_refs=required non-empty" in prompt
    assert "body={conclusion, rationale, answer_type?, finding_ids}; artifact_refs=forbidden" in prompt
    assert "blackboard_publish tool accepts one request object" in prompt
