import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from tga3.action_logs import read_action_log
from tga3.workers.action_hook import HookActionRecorder, interrupt_hook_actions, record_tool
from tga3.workers.action_journal import ActionJournal
from tga3.workers.blackboard_hook import checkpoint
from tga3.workers.cli_adapter import ClaudeAdapter, CodexAdapter
from tga3.workers.hook_service import HookService


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


def test_resident_journal_scans_once_per_hook_and_uses_a_fresh_start_snapshot(hook_env, monkeypatch):
    scans = []
    original = ActionJournal._snapshot

    def snapshot(journal):
        scans.append(journal)
        return original(journal)

    monkeypatch.setattr(ActionJournal, "_snapshot", snapshot)
    recorder = HookActionRecorder()
    recorder.start("generation-1")
    assert not scans
    recorder.record(tool_event())
    (hook_env / "first.txt").write_text("first")
    first = recorder.record(tool_event("PostToolUse", tool_response="first"))
    assert len(scans) == 2 and scans[0] is scans[1]
    assert first["artifact"]["attachments"] == ["first.txt"]
    # Files written between tools are not attributed to the following tool.
    (hook_env / "between.txt").write_text("external")
    recorder.record(tool_event(tool_use_id="call_1"))
    second = recorder.record(tool_event("PostToolUse", tool_use_id="call_1", tool_response="second"))
    assert len(scans) == 4 and all(journal is scans[0] for journal in scans)
    assert second["artifact"]["attachments"] == []
    recorder.interrupt("generation-1")
    assert len(scans) == 4 and recorder.journal is None


def test_overlapping_tools_keep_their_own_pre_execution_snapshots(hook_env):
    recorder = HookActionRecorder()
    recorder.start("generation-1")
    recorder.record(tool_event(tool_use_id="a"))
    (hook_env / "a.txt").write_text("a")
    recorder.record(tool_event(tool_use_id="b"))
    (hook_env / "b.txt").write_text("b")
    b = recorder.record(tool_event("PostToolUse", tool_use_id="b", tool_response="b"))
    a = recorder.record(tool_event("PostToolUse", tool_use_id="a", tool_response="a"))
    assert b["artifact"]["attachments"] == ["b.txt"]
    # Attachment detection describes changes within each tool's interval; shared
    # workspace snapshots cannot prove exclusive authorship for overlapping tools.
    assert a["artifact"]["attachments"] == ["a.txt", "b.txt"]
    assert a["action_id"] != b["action_id"]
    recorder.interrupt("generation-1")


def test_simultaneous_hook_threads_preserve_all_actions(hook_env):
    recorder = HookActionRecorder()
    recorder.start("generation-1")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: recorder.record(tool_event(tool_use_id=str(i))), range(12)))
        list(pool.map(lambda i: recorder.record(tool_event("PostToolUse", tool_use_id=str(i),
                                                           tool_response=str(i))), range(12)))
    actions = read_action_log(hook_env / "logs")["actions"]
    assert len(actions) == len({action["action_id"] for action in actions}) == 12
    assert all(action["status"] == "completed" for action in actions)
    assert not recorder.journal.pending and not recorder.journal.output_snapshots
    recorder.interrupt("generation-1")


@pytest.mark.parametrize("finish", ["post", "interrupt"])
def test_process_restart_recovers_pending_snapshot_and_id(hook_env, finish):
    first = HookActionRecorder()
    first.start("generation-1")
    first.record(tool_event())
    started = read_action_log(hook_env / "logs")["actions"][0]
    (hook_env / "partial.txt").write_text("partial")
    # A newly constructed recorder has no in-memory journal or snapshot.
    recovered = HookActionRecorder()
    recovered.start("generation-1")
    if finish == "post":
        recovered.record(tool_event("PostToolUse", tool_response="recovered"))
    recovered.interrupt("generation-1")
    actions = read_action_log(hook_env / "logs")["actions"]
    assert len(actions) == 1 and actions[0]["action_id"] == started["action_id"]
    assert actions[0]["status"] == ("completed" if finish == "post" else "interrupted")
    assert actions[0]["artifact"]["attachments"] == ["partial.txt"]
    assert not list((hook_env / ".tga3/action-hooks").glob("*.json"))


def test_finished_generation_ignores_late_hooks_and_reused_native_ids_are_distinct(hook_env):
    recorder = HookActionRecorder()
    recorder.start("generation-1")
    recorder.record(tool_event())
    recorder.interrupt("generation-1")
    assert recorder.record(tool_event("PostToolUse", tool_response="late")) is None
    recorder.start("generation-2")
    assert recorder.record(tool_event("PostToolUse", tool_response="late")) is None
    recorder.record(tool_event(_tga_generation="generation-2"))
    recorder.record(tool_event("PostToolUse", _tga_generation="generation-2", tool_response="new"))
    recorder.interrupt("generation-2")
    actions = read_action_log(hook_env / "logs")["actions"]
    assert [action["status"] for action in actions] == ["interrupted", "completed"]
    assert len({action["action_id"] for action in actions}) == 2


def test_http_hook_service_owns_the_same_journal_before_and_after_action(hook_env, monkeypatch):
    import httpx

    monkeypatch.setenv("TGA3_BLACKBOARD_MCP_URL", "http://fixture/mcp")
    scans = []
    original = ActionJournal._snapshot

    def snapshot(journal):
        scans.append(journal)
        return original(journal)

    monkeypatch.setattr(ActionJournal, "_snapshot", snapshot)
    service = HookService()
    service.client.close()
    service.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    service.actions.start("generation-1")
    try:
        with httpx.Client(trust_env=False) as client:
            pre = client.post(f"http://127.0.0.1:{service.port}/", json=tool_event())
            assert pre.status_code == 200
            (hook_env / "response.txt").write_text("result")
            post = client.post(f"http://127.0.0.1:{service.port}/",
                               json=tool_event("PostToolUse", tool_response="result"))
            assert post.status_code == 200
        action = read_action_log(hook_env / "logs")["actions"][0]
        assert len(scans) == 2 and scans[0] is scans[1] is service.actions.journal
        assert action["artifact"]["attachments"] == ["response.txt"]
        assert action["action_id"] in post.json()["hookSpecificOutput"]["additionalContext"]
    finally:
        service.actions.interrupt("generation-1")
        service.close()


async def test_cli_spawn_failure_releases_hook_generation(hook_env, monkeypatch):
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "command", lambda: (["fixture"], dict(os.environ)))

    async def fail(*args, **kwargs):
        raise OSError("fixture spawn failed")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fail)

    async def emit(*args):
        pass

    try:
        for _ in range(2):
            with pytest.raises(OSError, match="fixture spawn failed"):
                await adapter._run_once("fixture", emit)
            assert adapter.hook_service.actions.generation is None
            assert adapter.hook_service.actions.journal is None
    finally:
        adapter.hook_service.close()


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
