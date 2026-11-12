import asyncio
import json
import os
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

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
        "TGA3_TASK_ID": str(uuid4()),
        "TGA3_AGENT_ID": "worker-openai",
        "TGA3_AGENT_DISPLAY_NAME": "Worker",
        "TGA3_AGENT_RUNTIME": "codex_cli",
        "TGA3_WORKSPACE": str(tmp_path),
        "TGA3_LOGS": str(tmp_path / "logs"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return tmp_path


async def test_cards_only_reporter_uses_evidence_and_never_writes_markdown(cli_env, monkeypatch):
    from tga3.workers import openai_worker
    from tga3 import experience

    monkeypatch.setenv("TGA3_AGENT_ROLE", "reporter")
    monkeypatch.setenv("TGA3_PRODUCE_REPORT", "0")
    monkeypatch.setenv("TGA3_REPORT_EXPERIENCE", "1")
    monkeypatch.setenv("TGA3_SYSTEM_PROMPT", experience.CARDS_ONLY_INSTRUCTIONS)
    adapter = CodexAdapter()
    adapter.command()
    config = tomllib.loads((cli_env / ".codex/config.toml").read_text(encoding="utf-8"))
    instructions = config["developer_instructions"]
    assert "/evidence" in instructions and "Do not generate or save a Markdown report" in instructions
    assert "Return only Markdown" not in instructions and "Skills are in /skills" not in instructions
    prompts = []

    class Adapter:
        async def run_cycle(self, prompt, emit):
            prompts.append(prompt)
            return "Cards completed"

    checks = []

    def check(*args):
        checks.append(args)
        if len(checks) == 1:
            raise ValueError("missing card")

    monkeypatch.setattr(openai_worker, "CodexAdapter", Adapter)
    monkeypatch.setattr(experience, "check_report_file", check)

    def forbidden(*args, **kwargs):
        raise AssertionError("cards-only reporter must not write a Markdown report")

    monkeypatch.setattr(Path, "write_text", forbidden)
    await openai_worker.report()
    assert len(prompts) == 2 and "SAME session" in prompts[1]
    assert all("Do not generate" in prompt for prompt in prompts)


async def noop(*_args):
    pass


def test_codex_configuration_and_session_resume(cli_env):
    adapter = CodexAdapter()
    _, env = adapter.command()
    config = tomllib.loads((cli_env / ".codex/config.toml").read_text(encoding="utf-8"))
    assert config["model_providers"]["tga3"]["base_url"] == "http://provider/v1"
    assert "blackboard" not in config.get("mcp_servers", {})
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
        "import os,time;from pathlib import Path;Path('pid.tmp').write_text(str(os.getpid()));"
        "Path('pid.tmp').replace('pid');time.sleep(120)"
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
    assert "finding_publish" not in prompt
    assert "legacy finding" not in prompt
    assert "conclusion, rationale, answer_type?, finding_ids" in prompt
    assert "ONE JSON file in /findings/" in prompt
    assert "One execution is enough" in prompt
    assert "Do not schedule log reviews" in prompt
    assert "entry_id UUIDs from imported Finding receipts" in prompt


@pytest.mark.asyncio
async def test_worker_reports_idle_between_work_cycles(monkeypatch):
    for name, value in {
        "TGA3_TASK_ID": str(uuid4()),
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
        "TGA3_TASK_ID": str(uuid4()),
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
        "TGA3_TASK_ID": str(uuid4()), "TGA3_AGENT_ID": "worker-openai",
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


async def test_scout_resumes_same_session_and_only_extraction_has_the_json_tool(cli_env, monkeypatch):
    monkeypatch.setenv("TGA3_AGENT_ROLE", "scout")
    monkeypatch.setenv("TGA3_SCOUT_PHASE", "executing")
    monkeypatch.setenv("TGA3_LOGS", str(cli_env / "logs"))
    first = CodexAdapter()
    first.remember_session("execution-session")
    first.command()
    assert "mcp_servers" not in (cli_env / ".codex/config.toml").read_text()
    await first.event({"type": "item.completed", "item": {
        "type": "command_execution", "id": "probe", "command": "cat fixture.txt",
        "aggregated_output": "verified raw result", "exit_code": 0, "status": "completed",
    }}, noop)
    manifest = json.loads((cli_env / "logs/worker-openai-actions.json").read_text(encoding="utf-8"))
    raw = (cli_env / manifest["actions"][0]["artifact"]["output_path"]).read_text(encoding="utf-8")
    assert "verified raw result" in raw
    monkeypatch.setenv("TGA3_SCOUT_PHASE", "extracting")
    monkeypatch.setenv("TGA3_SCOUT_INTEL_MCP_URL", "http://control/scout-mcp/")
    second = CodexAdapter()
    assert second.session_id == "execution-session"
    command, _ = second.command()
    assert command[command.index("resume") + 1] == "execution-session"
    config = (cli_env / ".codex/config.toml").read_text()
    assert "[mcp_servers.intel]" in config and "[mcp_servers.blackboard]" not in config
    assert second.state_dir == first.state_dir
    assert not (cli_env / ".codex-extract").exists()
    count = len(list((cli_env / "logs").glob("*.json")))
    await second.event({"type": "item.completed", "item": {
        "type": "command_execution", "id": "read", "command": "cat /logs/action.json",
    }}, noop)
    assert len(list((cli_env / "logs").glob("*.json"))) == count
    monkeypatch.setenv("TGA3_SCOUT_PHASE", "merging")
    third = CodexAdapter()
    command, _ = third.command()
    assert third.session_id == "execution-session" and "resume" in command
    assert not third.journal.enabled
    assert "scout_merge_intel" in (cli_env / ".codex/config.toml").read_text()


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
Path('child-pid.tmp').write_text(str(child.pid))
Path('child-pid.tmp').replace('child-pid')
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
    try:
        child_state = status.read_text().rsplit(") ", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        pass  # The terminated child may be reaped before /proc can be read.
    else:
        assert child_state == "Z"


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


async def test_scout_runs_three_phases_on_one_adapter_and_retries_uncovered_json(cli_env, monkeypatch):
    from uuid import UUID

    from tga3.domain import ScoutActor
    from tga3.scout_intel import ScoutIntelCollection, ScoutIntelDocument, read_intels, save_intels
    from tga3.workers.openai_worker import scout, uncovered_actions, unmerged_intels

    monkeypatch.setenv("TGA3_AGENT_ROLE", "scout")
    monkeypatch.setenv("TGA3_AGENT_ID", "scout")
    monkeypatch.setenv("TGA3_SCOUT_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("TGA3_SCOUT_EXTRACT_TIMEOUT_SECONDS", "0.001")
    intels = cli_env / "intels"
    intels.mkdir()
    monkeypatch.setenv("TGA3_INTELS", str(intels))
    state = cli_env / ".tga3"
    state.mkdir()
    (state / "scout-extract-request").touch()
    (state / "scout-merge-request").touch()
    adapters, sessions, phases = [], [], []
    task_id = UUID(os.environ["TGA3_TASK_ID"])

    async def cycle(adapter, prompt, emit):
        adapters.append(adapter)
        phases.append(adapter.scout_phase)
        if adapter.scout_phase == "executing":
            adapter.remember_session("same-scout-session")
            for index in range(2):
                adapter.journal.record(str(index), "shell", inputs={"command": "fixture"},
                                       result={"output": str(index)}, status="completed")
        else:
            assert not adapter.journal.enabled and adapter.timeout is None
            merged = adapter.scout_phase == "merging"
            directory = intels / ("merged" if merged else "raw")
            collection = read_intels(directory, task_id, merged=merged) or ScoutIntelCollection(
                task_id=task_id, stage="merged" if merged else "raw",
                actor=ScoutActor(agent_id="scout", display_name="Scout"),
            )
            if merged:
                pending = unmerged_intels(intels)
                assert pending and pending[0]["id"] in prompt
                document = ScoutIntelDocument(id="scout-merged-intel-" + f"{len(phases):032x}", topic="fixture",
                                               body={"claim": "merged"}, merged_from_ids=[pending[0]["id"]])
            else:
                missing = uncovered_actions(adapter.journal.directory, intels)
                assert missing and missing[0] in prompt
                document = ScoutIntelDocument(id="scout-raw-intel-" + f"{len(phases):032x}", topic="fixture",
                                               body={"claim": "verified"}, source_action_ids=[missing[0]])
            collection.intels.append(document)
            save_intels(directory, collection)
        sessions.append(adapter.session_id)
        return "saved"

    monkeypatch.setattr(CodexAdapter, "run_cycle", cycle)
    await asyncio.wait_for(scout(), timeout=3)
    assert phases == ["executing", "extracting", "extracting", "merging", "merging"]
    assert len({id(adapter) for adapter in adapters}) == 1
    assert sessions == ["same-scout-session"] * 5
    assert not uncovered_actions(cli_env / "logs", intels)
    assert not unmerged_intels(intels)
    assert len(list((intels / "raw").glob("*.json"))) == 1
    assert len(read_intels(intels / "raw", task_id).intels) == 2
    for phase in ("executing", "extracting", "merging"):
        assert json.loads((state / f"scout-{phase}-result.json").read_text())["finish_reason"] == "completed"


@pytest.mark.parametrize("adapter_type", [CodexAdapter, ClaudeAdapter])
async def test_execution_log_keeps_tool_response_without_expanding_output_file(cli_env, adapter_type):
    output = cli_env / "result.txt"
    adapter = adapter_type()
    if adapter_type is CodexAdapter:
        item = {"type": "command_execution", "id": "redirected", "command": "fixture > result.txt",
                "aggregated_output": "", "exit_code": 0, "status": "completed"}
        await adapter.event({"type": "item.started", "item": {**item, "status": "in_progress"}}, noop)
        output.write_text("body exists only in the output file")
        await adapter.event({"type": "item.completed", "item": item}, noop)
    else:
        await adapter.event({"type": "assistant", "message": {"content": [{
            "type": "tool_use", "id": "redirected", "name": "Bash", "input": {"command": "fixture > result.txt"},
        }]}}, noop)
        output.write_text("body exists only in the output file")
        await adapter.event({"type": "user", "message": {"content": [{
            "type": "tool_result", "tool_use_id": "redirected", "content": "",
        }]}}, noop)
    record = json.loads((cli_env / "logs/worker-openai-actions.json").read_text(encoding="utf-8"))["actions"][0]
    assert record["artifact"]["attachments"] == ["result.txt"]
    assert (cli_env / record["artifact"]["output_path"]).read_text(encoding="utf-8") == ""
    assert "body exists only in the output file" not in json.dumps(record)
