import asyncio
import sys
from types import ModuleType

import pytest

from tga3.workers.claude_worker import ClaudeAdapter, ClaudeCliError
from tga3.workers.prompts import worker_instructions
from tga3.workers.runtime import AgentAdapter, WorkerSession


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
    module.UserMessage = type("UserMessage", (), {})
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
    module.UserMessage = type("UserMessage", (), {})
    module.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)

    async def emit(_method, _params):
        return None

    adapter = ClaudeAdapter()
    output = await adapter.run_cycle("continue", emit)
    assert "下一周期继续" in output
    assert adapter.session_id == "resumable-session"


@pytest.mark.asyncio
async def test_claude_worker_emits_tool_completion_from_user_message(monkeypatch):
    for name, value in {
        "TGA3_MODEL_NAME": "deepseek-v4-flash",
        "TGA3_API_KEY": "test-key",
        "TGA3_MAX_TURNS_PER_CYCLE": "12",
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

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ToolUseBlock:
        def __init__(self, tool_use_id, name, tool_input):
            self.id = tool_use_id
            self.name = name
            self.input = tool_input

    class ToolResultBlock:
        def __init__(self, tool_use_id):
            self.tool_use_id = tool_use_id
            self.is_error = False

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class UserMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        is_error = False
        result = "done"
        subtype = "success"
        session_id = "session"

    command = "curl -sS http://target/flag"

    async def query(**_kwargs):
        yield AssistantMessage([ToolUseBlock("tool-1", "Bash", {"command": command})])
        yield UserMessage([ToolResultBlock("tool-1")])
        yield ResultMessage()

    module = ModuleType("claude_agent_sdk")
    module.AssistantMessage = AssistantMessage
    module.ClaudeAgentOptions = ClaudeAgentOptions
    module.ResultMessage = ResultMessage
    module.TextBlock = TextBlock
    module.ToolResultBlock = ToolResultBlock
    module.ToolUseBlock = ToolUseBlock
    module.UserMessage = UserMessage
    module.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    emitted = []

    async def emit(method, params):
        emitted.append((method, params))

    await ClaudeAdapter().run_cycle("continue", emit)

    assert emitted == [
        (
            "agent.action.started",
            {
                "summary": "Bash",
                "tool": "Bash",
                "tool_use_id": "tool-1",
                "input": {"command": command},
            },
        ),
        (
            "agent.action.completed",
            {
                "summary": "Bash",
                "tool": "Bash",
                "tool_use_id": "tool-1",
                "input": {"command": command},
                "error": False,
            },
        ),
    ]


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


@pytest.mark.asyncio
async def test_worker_reports_idle_between_work_cycles(monkeypatch):
    for name, value in {
        "TGA3_TASK_ID": "task-id",
        "TGA3_AGENT_ID": "worker-openai",
        "TGA3_CONTROL_WS_URL": "ws://control",
        "TGA3_SYNC_SECONDS": "90",
        "TGA3_STARTUP_PROMPT": "start",
        "TGA3_PERIODIC_PROMPT": "continue",
        "TGA3_BLACKBOARD_CHANGED_PROMPT": "sync {latest_seq}",
    }.items():
        monkeypatch.setenv(name, value)

    class Adapter(AgentAdapter):
        async def run_cycle(self, prompt, emit):
            assert prompt == "start"
            return "done"

        async def set_model(self, params):
            return None

    session = WorkerSession(Adapter())
    emitted = []

    async def emit(method, params):
        emitted.append((method, params))
        if method == "agent.status" and params["state"] == "idle":
            session.stop.set()

    session.emit = emit
    await asyncio.wait_for(session._work(), timeout=1)

    assert emitted == [
        ("agent.status", {"state": "running"}),
        ("agent.action.started", {"summary": "开始一个工作周期"}),
        ("agent.output.delta", {"text": "done"}),
        ("agent.action.completed", {"summary": "工作周期完成"}),
        ("agent.status", {"state": "idle"}),
    ]
