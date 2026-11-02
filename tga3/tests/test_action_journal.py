import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tga3.action_logs import read_action_log
from tga3.errors import ContractError
from tga3.workers.action_journal import ActionJournal


def journal(tmp_path, **kwargs):
    return ActionJournal(tmp_path / "logs", identity={}, output_directory=tmp_path / "workspace", **kwargs)


def test_one_manifest_updates_started_action_and_keeps_body_out_of_log(tmp_path):
    log = journal(tmp_path, secret='key"private')
    log.record("tool-1", "shell", inputs={"command": "fixture"})
    started = read_action_log(tmp_path / "logs")["actions"][0]
    assert started["status"] == "started" and started["artifact"]["output_path"] is None
    output = "x" * 20_000 + 'key"private'
    log.record("tool-1", "shell", result={"aggregated_output": output, "exit_code": 7}, status="failed")
    document = read_action_log(tmp_path / "logs")
    assert len(document["actions"]) == 1
    entry = document["actions"][0]
    assert entry["action_id"] == started["action_id"]
    assert entry["action"]["command"] == "fixture" and entry["exit_code"] == 7
    assert "result" not in entry and "result_path" not in entry and "output_paths" not in entry
    assert entry["artifact"]["output_path"] == "result/" + entry["action_id"] + ".txt"
    assert (tmp_path / "workspace" / entry["artifact"]["output_path"]).read_text() == "x" * 20_000 + "[redacted]"
    assert list((tmp_path / "logs").glob("*.json")) == [tmp_path / "logs/agent-actions.json"]


def test_reused_native_ids_and_new_adapter_keep_distinct_action_ids(tmp_path):
    first = journal(tmp_path)
    first.record("item_0", "shell", result={"aggregated_output": "first"}, status="completed")
    second = journal(tmp_path)
    second.record("item_0", "shell", result={"aggregated_output": "second"}, status="completed")
    entries = read_action_log(tmp_path / "logs")["actions"]
    assert len(entries) == 2 and len({entry["action_id"] for entry in entries}) == 2
    assert {entry["tool_call_id"] for entry in entries} == {"item_0"}
    assert [(tmp_path / "workspace" / entry["artifact"]["output_path"]).read_text() for entry in entries] == [
        "first", "second",
    ]


def test_multiple_writers_preserve_every_action_in_the_same_manifest(tmp_path):
    writers = [journal(tmp_path) for _ in range(6)]

    def write(index):
        writers[index].record("item_0", "shell")
        writers[index].record("item_0", "shell", result={"aggregated_output": str(index)}, status="completed")

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(write, range(6)))
    entries = read_action_log(tmp_path / "logs")["actions"]
    assert len(entries) == len({entry["action_id"] for entry in entries}) == 6
    assert all(entry["status"] == "completed" for entry in entries)
    assert {(tmp_path / "workspace" / entry["artifact"]["output_path"]).read_text() for entry in entries} == {
        str(index) for index in range(6)
    }


def test_fast_commands_attachments_and_runtime_files_are_separate(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "unchanged.txt").write_text("same")
    log = journal(tmp_path)
    (workspace / "answer.txt").write_text("redirected body")
    # The CLI can finish a fast redirect before its start event is consumed.
    log.record("redirect", "shell", inputs={"command": "fixture > answer.txt"})
    (workspace / ".codex").mkdir()
    (workspace / ".codex/config.toml").write_text("runtime state")
    log.record("redirect", "shell", result={"aggregated_output": ""}, status="completed")
    log.record("next", "shell", result={"aggregated_output": "next"}, status="completed")
    first, second = read_action_log(tmp_path / "logs")["actions"]
    assert first["artifact"]["attachments"] == ["answer.txt"]
    assert (workspace / first["artifact"]["output_path"]).read_text() == ""
    assert second["artifact"]["attachments"] == []


def test_interruption_keeps_partial_files_and_explicit_body_status(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    log = journal(tmp_path)
    log.record("unfinished", "shell")
    (workspace / "partial.bin").write_bytes(b"partial")
    log.interrupt_pending()
    entry = read_action_log(tmp_path / "logs")["actions"][0]
    assert entry["status"] == "interrupted"
    assert entry["artifact"]["attachments"] == ["partial.bin"]
    body = json.loads((workspace / entry["artifact"]["output_path"]).read_text())
    assert body["result_received"] is False
    assert not log.pending


@pytest.mark.parametrize("result,expected", [
    ({"type": "tool_result", "content": "claude body"}, "claude body"),
    ({"type": "mcp_tool_call", "result": {"saved": True}}, {"saved": True}),
])
def test_text_and_structured_bodies_are_saved_in_result_directory(tmp_path, result, expected):
    log = journal(tmp_path)
    log.record("tool", "fixture", result=result, status="completed")
    entry = read_action_log(tmp_path / "logs")["actions"][0]
    path = tmp_path / "workspace" / entry["artifact"]["output_path"]
    assert path.parent == tmp_path / "workspace/result"
    body = path.read_text()
    assert (json.loads(body) if path.suffix == ".json" else body) == expected


def test_disabled_extraction_does_not_create_a_second_log(tmp_path):
    log = journal(tmp_path, enabled=False)
    log.record("read", "shell", result={"aggregated_output": "read"}, status="completed")
    assert not (tmp_path / "logs").exists()


def test_manifest_rejects_wrong_identity_and_duplicate_logical_ids(tmp_path):
    log = journal(tmp_path)
    log.record("tool", "shell", result={"aggregated_output": "body"}, status="completed")
    with pytest.raises(ContractError, match="task"):
        read_action_log(tmp_path / "logs", task_id="another-task")
    document = read_action_log(tmp_path / "logs")
    document["actions"].append(document["actions"][0])
    (tmp_path / "logs/agent-actions.json").write_text(json.dumps(document))
    with pytest.raises(ContractError, match="unique"):
        read_action_log(tmp_path / "logs")
