from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from tga.contracts import ExecutionPolicy, SessionFile, SessionInput, TGATask
from tga.evidence.store import EvidenceStore
from tga.inputs import MAX_MODEL_IMAGE_BYTES, SessionWorkspace
from tga.runtime.agent_session import AgentToolSession
from tga.runtime.context import SessionContextBuilder, build_working_messages
from tga.tools.mcp_manager import MCPManager


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _file(data: bytes, *, kind: str = "hint", name: str = "image.png", media_kind: str = "image") -> SessionFile:
    token = "a" * 32 if kind == "task" else "b" * 32
    folder = "task" if kind == "task" else "hints"
    return SessionFile(
        id=f"asset_{token}",
        originalName=name,
        storedName=f"{token}{Path(name).suffix}",
        relativePath=f"inputs/{folder}/{token}{Path(name).suffix}",
        mimeType="image/png" if media_kind == "image" else "text/plain",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        kind=kind,
        mediaKind=media_kind,
    )


def _task(task_file: SessionFile, hint_file: SessionFile | None = None) -> TGATask:
    return TGATask(
        id="context_v4",
        name="context",
        mode="reverse_engineering",
        goal="inspect files",
        mode_config={"mode": "reverse_engineering"},
        execution_policy=ExecutionPolicy(),
        session_input=SessionInput(
            taskFiles=[task_file],
            hint={"text": "Use the diagram.", "files": [hint_file] if hint_file else []},
        ),
        schema_version=4,
    )


def test_context_builder_emits_real_image_block_and_working_context_preserves_it(tmp_path: Path) -> None:
    workspace = SessionWorkspace(tmp_path / "context_v4")
    workspace.ensure()
    task_file = _file(b"sample", kind="task", name="sample.txt", media_kind="text")
    hint_file = _file(PNG)
    workspace.path_for(task_file).write_bytes(b"sample")
    workspace.path_for(hint_file).write_bytes(PNG)
    task = _task(task_file, hint_file)

    initial = SessionContextBuilder(task=task, workspace=workspace.root, supports_vision=True).build()[0]
    assert isinstance(initial["content"], list)
    assert initial["content"][1]["type"] == "image_url"
    assert initial["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    working, _ = build_working_messages(
        [{"role": "system", "content": "system"}, initial],
        task=task.model_dump(mode="json"),
        strategy_cards=[],
        memory=[],
    )
    assert working[1] == initial
    assert working[1]["content"][1]["type"] == "image_url"


def test_text_only_context_keeps_image_path_and_explicit_analysis_guidance(tmp_path: Path) -> None:
    workspace = SessionWorkspace(tmp_path / "context_v4")
    workspace.ensure()
    task_file = _file(b"sample", kind="task", name="sample.txt", media_kind="text")
    hint_file = _file(PNG)
    workspace.path_for(task_file).write_bytes(b"sample")
    workspace.path_for(hint_file).write_bytes(PNG)
    message = SessionContextBuilder(task=_task(task_file, hint_file), workspace=workspace.root, supports_vision=False).build()[0]

    assert len(message["content"]) == 1
    markdown = message["content"][0]["text"]
    assert hint_file.container_path in markdown
    assert "text-only" in markdown and "image-analysis/OCR" in markdown


def test_context_builder_omits_image_over_model_limit_but_keeps_path_guidance(tmp_path: Path) -> None:
    workspace = SessionWorkspace(tmp_path / "context_v4")
    workspace.ensure()
    task_file = _file(b"sample", kind="task", name="sample.txt", media_kind="text")
    oversized = _file(PNG)
    oversized = oversized.model_copy(update={"size": MAX_MODEL_IMAGE_BYTES + 1})
    workspace.path_for(task_file).write_bytes(b"sample")
    workspace.path_for(oversized).write_bytes(PNG)

    message = SessionContextBuilder(task=_task(task_file, oversized), workspace=workspace.root, supports_vision=True).build()[0]
    assert len(message["content"]) == 1
    assert oversized.container_path in message["content"][0]["text"]
    assert "Larger images remain available by path" in message["content"][0]["text"]


def test_context_manifest_is_auditable_and_excludes_legacy_mcp_acl(tmp_path: Path) -> None:
    workspace = SessionWorkspace(tmp_path / "context_v4")
    workspace.ensure()
    task_file = _file(b"sample", kind="task", name="sample.txt", media_kind="text")
    workspace.path_for(task_file).write_bytes(b"sample")
    task = _task(task_file)
    markdown = SessionContextBuilder(task=task, workspace=workspace.root, supports_vision=False).markdown()

    assert task_file.sha256 in markdown
    assert task_file.container_path in markdown
    policy_json = markdown.split("## Execution Boundaries", 1)[1]
    assert '"mcp"' not in policy_json
    working, _ = build_working_messages(
        [{"role": "system", "content": "system"}, {"role": "user", "content": "initial"}],
        task=task.model_dump(mode="json"), strategy_cards=[], memory=[],
    )
    governance = json.loads(working[2]["content"])
    assert governance["input_manifest"]["task_files"][0]["id"] == task_file.id
    assert governance["input_manifest"]["hint_text"] == "Use the diagram."


def test_first_agent_provider_call_receives_initial_image_block(tmp_path: Path) -> None:
    class CaptureModel:
        model = "vision-test"
        supports_vision = True

        def __init__(self) -> None:
            self.calls: list[list[dict]] = []

        def chat_tools(self, messages, *, tools, temperature=0.2):
            self.calls.append(messages)
            return {"message": {"role": "assistant", "content": "Need another turn.", "tool_calls": []}, "finish_reason": "stop"}

    run_root = tmp_path / "runs"
    workspace = SessionWorkspace(run_root / "context_v4")
    workspace.ensure()
    task_file = _file(b"sample", kind="task", name="sample.txt", media_kind="text")
    hint_file = _file(PNG)
    workspace.path_for(task_file).write_bytes(b"sample")
    workspace.path_for(hint_file).write_bytes(PNG)
    task = _task(task_file, hint_file)
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    config = tmp_path / "empty-mcp.json"
    config.write_text('{"version":1,"servers":{}}', encoding="utf-8")
    model = CaptureModel()
    try:
        AgentToolSession(
            task=task,
            store=store,
            run_root=run_root,
            client=model,
            executor=object(),
            max_turns=1,
            mcp_manager=MCPManager(config_path=config, cache_path=tmp_path / "mcp-cache.json"),
        ).run()
    finally:
        store.close()

    assert len(model.calls) == 1
    initial_user = model.calls[0][1]
    assert initial_user["role"] == "user"
    assert any(block.get("type") == "image_url" for block in initial_user["content"])
