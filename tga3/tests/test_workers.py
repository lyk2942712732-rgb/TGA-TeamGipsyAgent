import asyncio
import os
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tga3.workers.cli_adapter import ClaudeAdapter, CliError, CodexAdapter
from tga3.workers.prompts import worker_instructions
from tga3.workers.runtime import AgentAdapter, WorkerSession


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    values = {
        "TGA3_MODEL_NAME": "test-model",
        "TGA3_API_KEY": "secret-key",
        "TGA3_PROVIDER_PROTOCOL": "openai_responses",
        "TGA3_BASE_URL": "http://provider/v1",
        "TGA3_MAX_TURNS_PER_CYCLE": "3",
        "TGA3_BLACKBOARD_MCP_URL": "http://blackboard/mcp/",
        "TGA3_SYSTEM_PROMPT": "Work independently.",
        "TGA3_TASK_ID": "task-id",
        "TGA3_AGENT_ID": "worker-openai",
        "TGA3_AGENT_DISPLAY_NAME": "Worker",
        "TGA3_AGENT_RUNTIME": "codex_cli",
        "TGA3_WORKSPACE": str(tmp_path),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return tmp_path


async def noop(*_args):
    pass


def test_codex_configuration_and_session_resume(cli_env):
    adapter = CodexAdapter()
    _, env = adapter.command()
    config = tomllib.loads((cli_env / ".codex/config.toml").read_text(encoding="utf-8"))
    assert config["model_providers"]["tga3"]["base_url"] == "http://provider/v1"
    assert config["mcp_servers"]["blackboard"]["required"]
    assert "secret-key" not in str(config)
    assert env["CODEX_API_KEY"] == "secret-key"
    adapter.remember_session("session-1")
    resumed = CodexAdapter()
    command, _ = resumed.command()
    assert command[:4] == ["codex", "exec", "resume", "session-1"]


@pytest.mark.asyncio
async def test_cli_subprocess_parses_events_and_preserves_resume(cli_env):
    script = cli_env / "cli.py"
    script.write_text(
        """import json,sys
assert sys.stdin.read() == 'continue'
for event in [
 {'type':'thread.started','thread_id':'session-1'},
 {'type':'item.completed','item':{'type':'agent_message','text':'done'}},
 {'type':'turn.completed'}]:
 print(json.dumps(event),flush=True)
""",
        encoding="utf-8",
    )
    adapter = CodexAdapter()
    adapter.command = lambda: ([sys.executable, str(script)], dict(os.environ))
    events = []

    async def emit(method, params):
        events.append((method, params))

    assert await adapter.run_cycle("continue", emit) == "done"
    assert CodexAdapter().session_id == "session-1"
    assert any(method == "agent.session" for method, _ in events)


@pytest.mark.asyncio
async def test_cli_failure_exposes_diagnostics_without_credentials(cli_env):
    script = cli_env / "fail.py"
    script.write_text("import sys;sys.stdin.read();print('invalid secret-key',file=sys.stderr);sys.exit(1)")
    adapter = CodexAdapter()
    adapter.command = lambda: ([sys.executable, str(script)], dict(os.environ))
    with pytest.raises(CliError, match="invalid") as raised:
        await adapter.run_cycle("continue", noop)
    assert "secret-key" not in str(raised.value)


@pytest.mark.asyncio
async def test_claude_max_turns_is_resumable(cli_env, monkeypatch):
    monkeypatch.setenv("TGA3_PROVIDER_PROTOCOL", "anthropic")
    adapter = ClaudeAdapter()
    await adapter.event(
        {"type": "result", "session_id": "claude-session", "is_error": True, "subtype": "error_max_turns"}, noop
    )
    assert not adapter.failure
    assert adapter.completed
    assert "下一周期继续" in adapter.final_output
    assert ClaudeAdapter().session_id == "claude-session"


@pytest.mark.asyncio
async def test_cli_cancellation_stops_the_process(cli_env):
    script = cli_env / "sleep.py"
    script.write_text(
        "import os,time;from pathlib import Path;Path('pid').write_text(str(os.getpid()));time.sleep(120)"
    )
    adapter = CodexAdapter()
    adapter.command = lambda: ([sys.executable, str(script)], dict(os.environ))
    task = asyncio.create_task(adapter.run_cycle("continue", noop))
    for _ in range(200):
        if (cli_env / "pid").is_file():
            break
        await asyncio.sleep(0.01)
    assert (cli_env / "pid").is_file()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 8)
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.kill(int((cli_env / "pid").read_text()), 0)


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
    assert "intel: body={claim, detail?}; artifact_refs=optional" not in prompt
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


@pytest.mark.asyncio
async def test_worker_coalesces_blackboard_notifications_to_the_latest_sequence(monkeypatch):
    for name, value in {
        "TGA3_TASK_ID": "task-id",
        "TGA3_AGENT_ID": "worker-openai",
        "TGA3_CONTROL_WS_URL": "ws://control",
        "TGA3_SYNC_SECONDS": "90",
        "TGA3_STARTUP_PROMPT": "start",
        "TGA3_PERIODIC_PROMPT": "continue",
        "TGA3_BLACKBOARD_CHANGED_PROMPT": "sync through {latest_seq}",
    }.items():
        monkeypatch.setenv(name, value)

    class Adapter(AgentAdapter):
        async def run_cycle(self, prompt, emit):
            return ""

        async def set_model(self, params):
            return None

    session = WorkerSession(Adapter())
    await session._queue_blackboard_update(4)
    await session._queue_blackboard_update(7)
    await session._queue_blackboard_update(6)

    assert session.queue.qsize() == 1
    marker = await session.queue.get()
    assert marker is not None and not isinstance(marker, str)
    assert session.pending_blackboard_seq == 7


async def test_worker_continues_immediately_instead_of_waiting_90_seconds(monkeypatch):
    for name, value in {
        "TGA3_TASK_ID": "task-id", "TGA3_AGENT_ID": "worker-openai",
        "TGA3_CONTROL_WS_URL": "ws://control", "TGA3_STARTUP_PROMPT": "start",
        "TGA3_PERIODIC_PROMPT": "continue", "TGA3_BLACKBOARD_CHANGED_PROMPT": "sync {latest_seq}",
    }.items():
        monkeypatch.setenv(name, value)
    prompts = []

    class Adapter(AgentAdapter):
        async def run_cycle(self, prompt, emit):
            prompts.append(prompt)
            if len(prompts) == 3:
                session.stop.set()
            return "progress"

        async def set_model(self, params):
            pass

    session = WorkerSession(Adapter())
    await asyncio.wait_for(session._work(), timeout=0.5)
    assert prompts == ["start", "continue", "continue"]


@pytest.mark.parametrize("role", ["worker", "scout", "reporter"])
async def test_recovery_keeps_cli_session_for_every_role(cli_env, monkeypatch, role):
    monkeypatch.setenv("TGA3_AGENT_ROLE", role)
    adapter = CodexAdapter()
    adapter.remember_session("retained-session")
    calls = []

    async def once(prompt, emit):
        calls.append(prompt)
        if len(calls) == 1:
            raise CliError("simulated stall")
        assert adapter.session_id == "retained-session"
        assert "resume" in adapter.command()[0]
        return "recovered"

    async def no_wait(*args):
        pass

    monkeypatch.setattr(adapter, "_run_once", once)
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    assert await adapter.run_cycle("continue", noop) == "recovered"
    assert CodexAdapter().session_id == "retained-session"


def test_claude_has_no_fixed_turn_cap_and_installs_worker_hooks(cli_env, monkeypatch):
    monkeypatch.setenv("TGA3_AGENT_ROLE", "worker")
    monkeypatch.setenv("TGA3_PROVIDER_PROTOCOL", "anthropic")
    command, _ = ClaudeAdapter().command()
    assert "--max-turns" not in command
    assert "PreToolUse" in command[command.index("--settings") + 1]


async def test_inactivity_watchdog_stops_hung_cli_without_losing_session(cli_env, monkeypatch):
    monkeypatch.setenv("TGA3_CLI_IDLE_TIMEOUT_SECONDS", "0.05")
    script = cli_env / "hung.py"
    script.write_text(
        "import json,sys,time;sys.stdin.read();"
        "print(json.dumps({'type':'thread.started','thread_id':'keep-context'}),flush=True);time.sleep(30)"
    )
    adapter = CodexAdapter()
    adapter.remember_session("keep-context")
    adapter.command = lambda: ([sys.executable, str(script)], dict(os.environ))
    with pytest.raises(CliError, match="inactivity"):
        await asyncio.wait_for(adapter._run_once("continue", noop), timeout=8)
    assert CodexAdapter().session_id == "keep-context"


async def test_identical_tool_result_loop_is_interrupted_and_keeps_context(cli_env, monkeypatch):
    clock = [0]

    def monotonic():
        clock[0] += 20
        return clock[0]

    # Replace only this module's clock; asyncio keeps its real clock.
    monkeypatch.setattr("tga3.workers.cli_adapter.time", SimpleNamespace(monotonic=monotonic))
    script = cli_env / "loop.py"
    script.write_text("""import json,sys,time
sys.stdin.read()
print(json.dumps({'type':'thread.started','thread_id':'loop-context'}),flush=True)
for i in range(12):
 print(json.dumps({'type':'item.completed','item':{
  'type':'command_execution','id':str(i),'command':'retry same command',
  'aggregated_output':'same failure','exit_code':1,'status':'completed'}}),flush=True)
time.sleep(30)
""")
    adapter = CodexAdapter()
    adapter.command = lambda: ([sys.executable, str(script)], dict(os.environ))
    with pytest.raises(CliError, match="identical tool/result"):
        await asyncio.wait_for(adapter._run_once("continue", noop), timeout=8)
    assert CodexAdapter().session_id == "loop-context"


async def test_repeated_short_replies_with_real_tool_progress_do_not_trigger_recovery(cli_env, monkeypatch):
    adapter = CodexAdapter()
    adapter.remember_session("working-session")

    async def once(*args):
        adapter.tool_progress = True
        return "done"

    monkeypatch.setattr(adapter, "_run_once", once)
    monkeypatch.setattr("tga3.workers.cli_adapter.time", SimpleNamespace(monotonic=lambda: 1000))
    adapter.output_history_since = 0
    for _ in range(12):
        assert await adapter.run_cycle("continue", noop) == "done"
    assert not adapter.output_history


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Linux deployment process-tree cleanup")
async def test_cancellation_also_stops_tool_in_separate_session(cli_env):
    script = cli_env / "detached.py"
    script.write_text("""import os,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],start_new_session=True)
Path('child-pid').write_text(str(child.pid))
time.sleep(120)
""")
    adapter = CodexAdapter()
    adapter.command = lambda: ([sys.executable, str(script)], dict(os.environ))
    task = asyncio.create_task(adapter.run_cycle("continue", noop))
    for _ in range(200):
        if (cli_env / "child-pid").is_file():
            break
        await asyncio.sleep(0.01)
    assert (cli_env / "child-pid").is_file()
    child = int((cli_env / "child-pid").read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 8)
    status = Path(f"/proc/{child}/stat")
    if status.exists():
        assert status.read_text().rsplit(") ", 1)[1].split()[0] == "Z"


@pytest.mark.asyncio
async def test_model_switch_overrides_resumed_cli_model_and_credentials(cli_env):
    adapter = CodexAdapter()
    adapter.remember_session("session-1")
    await adapter.set_model(
        {
            "model_name": "new-model",
            "api_key": "new-key",
            "base_url": "http://new-provider/v1",
            "protocol": "openai_responses",
        }
    )
    command, env = adapter.command()
    assert command[command.index("--model") + 1] == "new-model"
    assert env["CODEX_API_KEY"] == env["TGA3_API_KEY"] == "new-key"
    assert "old-provider" not in (cli_env / ".codex/config.toml").read_text()


@pytest.mark.asyncio
async def test_stopping_a_paused_worker_does_not_launch_a_queued_cli(monkeypatch):
    for key, value in {
        "TGA3_TASK_ID": "task",
        "TGA3_AGENT_ID": "worker-openai",
        "TGA3_CONTROL_WS_URL": "ws://control",
        "TGA3_SYNC_SECONDS": "90",
        "TGA3_STARTUP_PROMPT": "start",
    }.items():
        monkeypatch.setenv(key, value)

    class Adapter(AgentAdapter):
        async def run_cycle(self, *_args):
            pytest.fail("CLI must not start after stop")

        async def set_model(self, _params):
            pass

    session = WorkerSession(Adapter())
    session.paused.clear()
    task = asyncio.create_task(session._work())
    await asyncio.sleep(0.01)
    session.stop.set()
    session.paused.set()
    await asyncio.wait_for(task, 1)
