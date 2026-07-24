from __future__ import annotations

import json
from pathlib import Path

from tga.capabilities.registry import build_default_registry
from tga.contracts import ActionResult, ActionSpec, ExecutionPolicy, ResourceProvenance, SessionFile, SessionInput, TGATask
from tga.evidence.store import EvidenceStore
from tga.inputs import SessionWorkspace
from tga.runtime.handlers import ActionRecorder, HandlerState, build_tool_handlers
from tga.runtime.tooling import ToolDispatcher
from tga.tools.mcp_manager import MCPManager


def _task() -> TGATask:
    return TGATask(id="dispatch", name="dispatch", mode="ctf", goal="dispatch")


def _call(name: str, arguments) -> dict:
    return {"id": "call_1", "function": {"name": name, "arguments": arguments}}


def test_dispatcher_parses_governance_and_routes_capability() -> None:
    seen = {}

    def capability_handler(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    dispatcher = ToolDispatcher(
        capability_handler=capability_handler,
        input_handler=lambda **_: {},
        mcp_handler=lambda **_: {},
        completion_handler=lambda **_: {},
    )
    result = dispatcher.dispatch(
        task=_task(),
        call=_call("tga_workspace_read", json.dumps({"relative_path": "input.txt", "_tga": {"rationale": "inspect"}})),
    )

    assert result == {"ok": True}
    assert seen["tool_name"] == "tga_workspace_read"
    assert seen["arguments"] == {"relative_path": "input.txt"}
    assert seen["governance"] == {"rationale": "inspect"}


def test_dispatcher_rejects_invalid_arguments_and_governance() -> None:
    dispatcher = ToolDispatcher(
        capability_handler=lambda **_: {"ok": True},
        input_handler=lambda **_: {},
        mcp_handler=lambda **_: {},
        completion_handler=lambda **_: {},
    )

    malformed = dispatcher.dispatch(task=_task(), call=_call("input_read", "{"))
    wrong_governance = dispatcher.dispatch(task=_task(), call=_call("input_read", {"_tga": "unsafe"}))

    assert malformed["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert wrong_governance["error"]["code"] == "INVALID_GOVERNANCE_METADATA"


def test_dispatcher_routes_finish_input_gateway_and_direct_mcp() -> None:
    routes: list[str] = []
    dispatcher = ToolDispatcher(
        capability_handler=lambda **_: routes.append("capability") or {},
        input_handler=lambda **_: routes.append("input") or {},
        mcp_handler=lambda *, direct, **_: routes.append("direct_mcp" if direct else "gateway_mcp") or {},
        completion_handler=lambda **_: routes.append("finish") or {},
        direct_mcp_names=lambda: {"mcp_fixture_read"},
    )

    dispatcher.dispatch(task=_task(), call=_call("finish_session", {}))
    dispatcher.dispatch(task=_task(), call=_call("input_read", {}))
    dispatcher.dispatch(task=_task(), call=_call("tga_mcp", {}))
    dispatcher.dispatch(task=_task(), call=_call("mcp_fixture_read", {}))

    assert routes == ["finish", "input", "gateway_mcp", "direct_mcp"]


def test_runtime_builds_distinct_concrete_handlers(tmp_path: Path) -> None:
    task = _task()
    store = EvidenceStore(tmp_path / "evidence.db")
    manager = MCPManager(cache_path=tmp_path / "mcp-cache.json")
    handlers = build_tool_handlers(
        task=task,
        store=store,
        run_root=tmp_path,
        client=object(),
        executor=object(),
        solver_id="solver_main",
        workspace=tmp_path / "workspace",
        mcp_manager=manager,
        mcp_snapshot=manager.snapshot_for_task(task, workspace=tmp_path / "workspace"),
        registry=build_default_registry(),
        tool_by_name={},
    )

    assert len({id(handlers.capability), id(handlers.inputs), id(handlers.mcp), id(handlers.completion)}) == 4
    assert handlers.capability.recorder is handlers.recorder
    assert handlers.mcp.artifacts is handlers.artifacts
    handlers.close()


def test_action_recorder_rolls_back_result_and_status_together(tmp_path: Path, monkeypatch) -> None:
    task = _task()
    store = EvidenceStore(tmp_path / "evidence.db")
    manager = MCPManager(cache_path=tmp_path / "mcp-cache.json")
    state = HandlerState(
        task=task,
        store=store,
        run_root=tmp_path,
        client=object(),
        executor=object(),
        solver_id="solver_main",
        workspace=tmp_path / "workspace",
        mcp_manager=manager,
        mcp_snapshot=manager.snapshot_for_task(task, workspace=tmp_path / "workspace"),
        registry=build_default_registry(),
        tool_by_name={},
    )
    recorder = ActionRecorder(state)
    action = ActionSpec(
        id="act_atomic",
        task_id=task.id,
        solver_id="solver_main",
        kind="tool",
        capability="workspace.read",
        target="input.txt",
        arguments={"relative_path": "input.txt"},
        rationale="inspect",
        risk="passive",
    )
    result = ActionResult(
        action_id=action.id,
        task_id=task.id,
        solver_id="solver_main",
        status="succeeded",
        summary="read",
    )
    recorder.start(action)

    monkeypatch.setattr(store, "update_action_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fault")))
    try:
        recorder.finish(action, result)
    except RuntimeError:
        pass

    persisted = store.list_actions(task.id)
    assert persisted[0]["status"] == "running"
    assert persisted[0]["result"] is None
    state.close()


def test_capability_result_event_failure_rolls_back_post_execution_state(tmp_path: Path, monkeypatch) -> None:
    task = _task()
    store = EvidenceStore(tmp_path / "evidence.db")
    store.create_task(task)
    manager = MCPManager(cache_path=tmp_path / "mcp-cache.json")

    class Executor:
        def execute(self, *, action, **_kwargs):
            return ActionResult(
                action_id=action.id,
                task_id=task.id,
                solver_id="solver_main",
                status="succeeded",
                summary="read",
            )

    handlers = build_tool_handlers(
        task=task,
        store=store,
        run_root=tmp_path,
        client=object(),
        executor=Executor(),
        solver_id="solver_main",
        workspace=tmp_path / "workspace",
        mcp_manager=manager,
        mcp_snapshot=manager.snapshot_for_task(task, workspace=tmp_path / "workspace"),
        registry=build_default_registry(),
        tool_by_name={"tga_workspace_read": "workspace.read"},
    )
    original_append = store.append_agent_event

    def fail_end(task_id, event_type, payload, *, solver_id=None):
        if event_type == "TOOL_EXECUTION_END":
            raise RuntimeError("inject end event failure")
        return original_append(task_id, event_type, payload, solver_id=solver_id)

    monkeypatch.setattr(store, "append_agent_event", fail_end)
    try:
        handlers.capability.handle(
            call={"id": "call_atomic"},
            tool_name="tga_workspace_read",
            arguments={"relative_path": "input.txt"},
            governance={"rationale": "inspect"},
        )
    except RuntimeError as exc:
        assert "end event failure" in str(exc)

    action = store.list_actions(task.id)[0]
    assert action["status"] == "running"
    assert action["result"] is None
    assert "TOOL_EXECUTION_END" not in {event.type for event in store.list_agent_events(task.id)}
    handlers.close()


def _input_runtime(tmp_path: Path, *, task_id: str = "input_evidence"):
    raw = b"challenge result: CTF{input_artifact_provenance}\n"
    digest = __import__("hashlib").sha256(raw).hexdigest()
    item = SessionFile(
        id=f"asset_{'a' * 32}",
        originalName="challenge.txt",
        storedName=f"{'a' * 32}.txt",
        relativePath=f"inputs/files/{'a' * 32}.txt",
        mimeType="text/plain",
        size=len(raw),
        sha256=digest,
        kind="task_input",
        mediaKind="text",
        provenance={
            "source": "user_upload",
            "created_at": "2026-07-24T00:00:00Z",
            "original_name": "challenge.txt",
        },
    )
    task = TGATask(
        id=task_id,
        name="input evidence",
        mode="ctf",
        goal="read and verify the supplied flag",
        flag_format=r"CTF\{[^}]+\}",
        schema_version=5,
        mode_config={"mode": "ctf", "flag_format": r"CTF\{[^}]+\}"},
        execution_policy=ExecutionPolicy(),
        session_input=SessionInput(files=[item]),
    )
    task_root = tmp_path / task.id
    workspace = SessionWorkspace(task_root)
    workspace.ensure()
    workspace.path_for(item).write_bytes(raw)
    store = EvidenceStore(task_root / "evidence.db")
    store.create_task(task)
    manager = MCPManager(cache_path=tmp_path / f"{task.id}-mcp-cache.json")
    handlers = build_tool_handlers(
        task=task,
        store=store,
        run_root=tmp_path,
        client=object(),
        executor=object(),
        solver_id="solver_main",
        workspace=workspace.root,
        mcp_manager=manager,
        mcp_snapshot=manager.snapshot_for_task(task, workspace=workspace.root),
        registry=build_default_registry(),
        tool_by_name={},
    )
    dispatcher = ToolDispatcher(
        capability_handler=handlers.capability.handle,
        input_handler=handlers.inputs.handle,
        mcp_handler=handlers.mcp.handle,
        completion_handler=handlers.completion.handle,
        direct_mcp_names=handlers.mcp.direct_names,
    )
    return task, item, raw, store, handlers, dispatcher


def test_input_read_persists_task_owned_provenance_and_passes_completion_gate(tmp_path: Path) -> None:
    task, item, _, store, handlers, dispatcher = _input_runtime(tmp_path)

    result = dispatcher.dispatch(
        task=task,
        call=_call("input_read", {
            "input_id": item.id,
            "offset": 0,
            "limit": 4096,
            "_tga": {"rationale": "read the supplied challenge evidence"},
        }),
    )

    assert result["ok"] is True
    assert result["artifact_ids"] == [result["artifact_id"]]
    artifact = store.get_artifact(result["artifact_id"])
    assert artifact is not None
    assert artifact.task_id == task.id
    assert artifact.input_id == item.id
    assert artifact.tool == "input_read"
    assert artifact.target == item.container_path
    assert artifact.provenance == {
        "source": "user_upload",
        "created_at": "2026-07-24T00:00:00Z",
        "original_name": "challenge.txt",
        "parent_input_id": None,
        "input_id": item.id,
        "operation": "input_read",
        "source_sha256": item.sha256,
        "source_size": item.size,
        "workspace_path": item.relative_path,
        "container_path": item.container_path,
        "immutable": True,
        "offset": 0,
        "next_offset": item.size,
        "eof": True,
    }
    assert "CTF{input_artifact_provenance}" in handlers.artifacts.text(task.id, artifact)
    assert store.get_artifact_index(artifact.id) is not None
    action = store.list_actions(task.id)[0]
    assert action["capability"] == "input_read"
    assert action["status"] == "succeeded"
    assert action["result"]["artifact_ids"] == [artifact.id]
    event_types = [event.type for event in store.list_agent_events(task.id)]
    assert event_types == [
        "MANAGER_DECISION",
        "TOOL_EXECUTION_START",
        "ARTIFACT_SAVED",
        "INPUT_ACCESSED",
        "TOOL_EXECUTION_END",
    ]

    completed = dispatcher.dispatch(task=task, call=_call("finish_session", {
        "summary": "verified immutable input evidence",
        "flag": "CTF{input_artifact_provenance}",
        "evidence_artifact_ids": [artifact.id],
    }))
    assert completed["ok"] is True
    assert completed["validation"]["code"] == "CTF_FLAG_VERIFIED"
    assert completed["validation"]["evidence_artifact_ids"] == [artifact.id]
    handlers.close()
    store.close()


def test_input_materialize_artifact_is_source_bytes_without_artifact_chain(tmp_path: Path) -> None:
    task, item, raw, store, handlers, dispatcher = _input_runtime(tmp_path, task_id="input_materialized")

    result = dispatcher.dispatch(
        task=task,
        call=_call("input_materialize", {"input_id": item.id}),
    )

    artifact = store.get_artifact(result["artifact_id"])
    assert artifact is not None
    assert artifact.sha256 == item.sha256
    assert artifact.provenance["source_sha256"] == item.sha256
    assert "parent_artifact_id" not in artifact.provenance
    assert (tmp_path / task.id / "workspace" / "artifacts" / artifact.path).read_bytes() == raw
    assert store.get_artifact_index(artifact.id) is None
    handlers.close()
    store.close()


def test_identical_input_bytes_keep_distinct_materialization_provenance(tmp_path: Path) -> None:
    task, first, raw, store, handlers, dispatcher = _input_runtime(tmp_path, task_id="identical_inputs")
    second = first.model_copy(update={
        "id": f"asset_{'b' * 32}",
        "stored_name": f"{'b' * 32}.txt",
            "relative_path": f"inputs/files/{'b' * 32}.txt",
        "provenance": ResourceProvenance(
            source="user_upload",
            created_at="2026-07-24T00:01:00Z",
            original_name="copy.txt",
        ),
    })
    task = task.model_copy(update={"session_input": SessionInput(files=[first, second])})
    store.update_task(task)
    second_path = SessionWorkspace(tmp_path / task.id).path_for(second)
    second_path.parent.mkdir(parents=True, exist_ok=True)
    second_path.write_bytes(raw)
    handlers.close()
    manager = MCPManager(cache_path=tmp_path / "identical-inputs-mcp-cache.json")
    handlers = build_tool_handlers(
        task=task,
        store=store,
        run_root=tmp_path,
        client=object(),
        executor=object(),
        solver_id="solver_main",
        workspace=tmp_path / task.id / "workspace",
        mcp_manager=manager,
        mcp_snapshot=manager.snapshot_for_task(task, workspace=tmp_path / task.id / "workspace"),
        registry=build_default_registry(),
        tool_by_name={},
    )
    dispatcher = ToolDispatcher(
        capability_handler=handlers.capability.handle,
        input_handler=handlers.inputs.handle,
        mcp_handler=handlers.mcp.handle,
        completion_handler=handlers.completion.handle,
        direct_mcp_names=handlers.mcp.direct_names,
    )

    first_result = dispatcher.dispatch(task=task, call=_call("input_materialize", {"input_id": first.id}))
    second_result = dispatcher.dispatch(task=task, call=_call("input_materialize", {"input_id": second.id}))

    assert first_result["artifact_id"] != second_result["artifact_id"]
    first_artifact = store.get_artifact(first_result["artifact_id"])
    second_artifact = store.get_artifact(second_result["artifact_id"])
    assert first_artifact is not None and second_artifact is not None
    assert first_artifact.sha256 == second_artifact.sha256 == first.sha256
    assert first_artifact.input_id == first.id
    assert second_artifact.input_id == second.id
    handlers.close()
    store.close()


def test_input_artifact_event_failure_rolls_back_metadata_and_removes_file(tmp_path: Path, monkeypatch) -> None:
    task, item, _, store, handlers, dispatcher = _input_runtime(tmp_path, task_id="input_artifact_rollback")
    original_append = store.append_agent_event

    def fail_artifact_event(task_id, event_type, payload, *, solver_id=None):
        if event_type == "ARTIFACT_SAVED":
            raise RuntimeError("inject artifact event failure")
        return original_append(task_id, event_type, payload, solver_id=solver_id)

    monkeypatch.setattr(store, "append_agent_event", fail_artifact_event)
    with __import__("pytest").raises(RuntimeError, match="artifact event failure"):
        dispatcher.dispatch(task=task, call=_call("input_read", {"input_id": item.id}))

    assert store.task_snapshot(task.id)["artifacts"] == []
    assert store.list_artifact_indexes(task.id) == []
    assert list((tmp_path / task.id / "workspace" / "artifacts").iterdir()) == []
    action = store.list_actions(task.id)[0]
    assert action["status"] == "running"
    assert action["result"] is None
    handlers.close()
    store.close()
