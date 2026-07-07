from tga.contracts import Finding, TGATask
from tga.core.evidence_gate import finding_ok


def _task():
    return TGATask(
        id="task_1",
        name="audit",
        mode="web_audit",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        allow_active_scan=True,
        goal="audit",
    )


def test_finding_without_artifact_rejected():
    finding = Finding(id="finding_1", task_id="task_1", title="xss", target="http://127.0.0.1:8080", severity="medium")
    assert not finding_ok(finding, task=_task(), artifact_text=None)


def test_finding_with_evidence_passes():
    finding = Finding(
        id="finding_1",
        task_id="task_1",
        title="xss",
        target="http://127.0.0.1:8080",
        severity="medium",
        evidence_artifact_id="artifact_abc",
        evidence_excerpt="Reflected payload",
    )
    assert finding_ok(finding, task=_task(), artifact_text="HTTP response: Reflected payload")

