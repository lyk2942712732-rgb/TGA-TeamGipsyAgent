from __future__ import annotations

from pathlib import Path

from tga.contracts import Intent, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.tools.mcp_catalog import MCPCatalog, MCPServerSpec, MCPToolSpec
from tga.tools.mcp_client import MCPCallResult
from tga.tools.tool_runner import ToolRunner


class FakeClient:
    def call_tool(self, *, server, tool_name, arguments, volumes=None, timeout_seconds):
        return MCPCallResult(
            command=["fake", server.id, tool_name],
            stdout='{"ok": true}',
            stderr="",
            returncode=0,
        )


def _task() -> TGATask:
    return TGATask(
        id="task_12345678",
        name="demo",
        mode="penetration_test",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        intensity="normal",
        allow_active_scan=True,
        goal="demo",
    )


def _intent() -> Intent:
    return Intent(
        id="intent_12345678",
        task_id="task_12345678",
        kind="recon",
        target="http://127.0.0.1:8080",
        goal="demo",
    )


def test_tool_runner_saves_mcp_output_artifact(tmp_path: Path) -> None:
    catalog = MCPCatalog(
        hub_root=str(tmp_path),
        servers=[
            MCPServerSpec(
                id="nuclei-mcp",
                category="web-security",
                path="web-security/nuclei-mcp",
                image="nuclei-mcp:latest",
                tools=[MCPToolSpec(name="scan_target")],
            )
        ],
    )
    runner = ToolRunner(catalog=catalog, artifact_store=ArtifactStore(tmp_path), mcp_client=FakeClient())

    artifact = runner.run_tool(
        task=_task(),
        intent=_intent(),
        tool="nuclei",
        target="http://127.0.0.1:8080",
        args={"mcp_tool": "scan_target", "url": "http://127.0.0.1:8080"},
    )

    assert artifact.kind == "tool_output"
    assert artifact.tool == "nuclei-mcp"
    assert (tmp_path / artifact.path).exists()


def test_tool_runner_saves_policy_rejection_artifact(tmp_path: Path) -> None:
    catalog = MCPCatalog(
        hub_root=str(tmp_path),
        servers=[
            MCPServerSpec(
                id="nuclei-mcp",
                category="web-security",
                path="web-security/nuclei-mcp",
                image="nuclei-mcp:latest",
            )
        ],
    )
    task = _task().model_copy(update={"scope": ["example.com"]})
    runner = ToolRunner(catalog=catalog, artifact_store=ArtifactStore(tmp_path), mcp_client=FakeClient())

    artifact = runner.run_tool(task=task, intent=_intent(), tool="nuclei", target="http://127.0.0.1:8080", args={})

    body = (tmp_path / artifact.path).read_text(encoding="utf-8")
    assert "OUT_OF_SCOPE" in body
