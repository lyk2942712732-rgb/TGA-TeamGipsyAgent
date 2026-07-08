from pathlib import Path

from tga.contracts import Intent, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.workers.subprocess_worker import SubprocessWorker


def test_worker_blocks_required_tools_without_tool_runner(tmp_path: Path):
    task = TGATask(
        id="task_tools",
        name="tools-demo",
        mode="code_audit",
        target=".",
        scope=["."],
        intensity="passive",
        allow_active_scan=False,
        goal="scan",
    )
    intent = Intent(
        id="intent_tools",
        task_id=task.id,
        kind="code_scan",
        target=task.target,
        goal="run static tools",
        required_tools=["semgrep", "gitleaks"],
    )
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    worker = SubprocessWorker(artifact_store=artifact_store, tool_runner=None)

    result = worker.run(task=task, intent=intent, workspace=str(tmp_path / "work"))

    assert result.status == "blocked"
    assert result.errors == ["TOOL_RUNNER_UNAVAILABLE"]
    assert "semgrep,gitleaks" in artifact_store.read_text(result.artifacts[0].id)
