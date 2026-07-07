from pathlib import Path

from tga.contracts import Finding, Intent, TGATask, WorkerResult
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.orchestrator.scheduler import Scheduler
class StaticWorker:
    def __init__(self, result: WorkerResult):
        self.result = result

    def run(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult:
        return self.result


def _task() -> TGATask:
    return TGATask(
        id="task_gate",
        name="gate-demo",
        mode="ctf",
        target="http://127.0.0.1:8081",
        scope=["127.0.0.1:8081"],
        intensity="normal",
        allow_active_scan=True,
        goal="solve",
        flag_format=r"flag\{[^}]+\}",
    )


def _intent(task: TGATask) -> Intent:
    return Intent(
        id="intent_gate",
        task_id=task.id,
        kind="exploit_ctf",
        target=task.target,
        goal="recover flag",
        risk="active",
    )


def _stores(tmp_path: Path, task: TGATask) -> tuple[EvidenceStore, ArtifactStore]:
    run_root = tmp_path / "runs"
    artifact_store = ArtifactStore(run_root / task.id / "artifacts")
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    return store, artifact_store


def test_scheduler_confirms_flag_with_artifact(tmp_path: Path):
    task = _task()
    intent = _intent(task)
    store, artifact_store = _stores(tmp_path, task)
    artifact = artifact_store.save_text(
        task_id=task.id,
        intent_id=intent.id,
        kind="stdout",
        text="service returned flag{real_123}",
        tool="fake",
        target=task.target,
    )
    result = WorkerResult(
        task_id=task.id,
        intent_id=intent.id,
        status="ok",
        artifacts=[artifact],
        flags=["flag{real_123}"],
    )

    scheduler = Scheduler(
        store=store,
        worker=StaticWorker(result),
        run_root=str(tmp_path / "runs"),
    )
    scheduler.run_intent(task=task, intent=intent)

    snapshot = store.task_snapshot(task.id)
    assert snapshot["flags"][0]["value"] == "flag{real_123}"
    assert snapshot["flags"][0]["evidence_artifact_id"] == artifact.id


def test_scheduler_rejects_placeholder_flag(tmp_path: Path):
    task = _task()
    intent = _intent(task)
    store, artifact_store = _stores(tmp_path, task)
    artifact = artifact_store.save_text(
        task_id=task.id,
        intent_id=intent.id,
        kind="stdout",
        text="service returned flag{...}",
        tool="fake",
        target=task.target,
    )
    result = WorkerResult(
        task_id=task.id,
        intent_id=intent.id,
        status="ok",
        artifacts=[artifact],
        flags=["flag{...}"],
    )

    scheduler = Scheduler(
        store=store,
        worker=StaticWorker(result),
        run_root=str(tmp_path / "runs"),
    )
    scheduler.run_intent(task=task, intent=intent)

    snapshot = store.task_snapshot(task.id)
    assert snapshot["flags"] == []
    assert any(event["type"] == "GATE_REJECTED" for event in snapshot["events"])


def test_scheduler_confirms_finding_with_artifact(tmp_path: Path):
    task = TGATask(
        id="task_audit",
        name="audit-demo",
        mode="web_audit",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        intensity="normal",
        allow_active_scan=True,
        goal="audit",
    )
    intent = Intent(
        id="intent_verify",
        task_id=task.id,
        kind="verify",
        target=task.target,
        goal="verify reflected xss",
        risk="active",
    )
    store, artifact_store = _stores(tmp_path, task)
    artifact = artifact_store.save_text(
        task_id=task.id,
        intent_id=intent.id,
        kind="http_response",
        text="HTTP response contains Reflected payload",
        tool="fake",
        target=task.target,
    )
    finding = Finding(
        id="finding_xss",
        task_id=task.id,
        title="Reflected XSS",
        target=task.target,
        severity="medium",
        evidence_artifact_id=artifact.id,
        evidence_excerpt="Reflected payload",
    )
    result = WorkerResult(
        task_id=task.id,
        intent_id=intent.id,
        status="ok",
        artifacts=[artifact],
        findings=[finding],
    )

    scheduler = Scheduler(
        store=store,
        worker=StaticWorker(result),
        run_root=str(tmp_path / "runs"),
    )
    scheduler.run_intent(task=task, intent=intent)

    snapshot = store.task_snapshot(task.id)
    assert snapshot["findings"][0]["status"] == "confirmed"
    assert snapshot["findings"][0]["evidence_artifact_id"] == artifact.id


def test_scheduler_rejects_out_of_scope_finding(tmp_path: Path):
    task = TGATask(
        id="task_audit",
        name="audit-demo",
        mode="web_audit",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        intensity="normal",
        allow_active_scan=True,
        goal="audit",
    )
    intent = Intent(
        id="intent_verify",
        task_id=task.id,
        kind="verify",
        target=task.target,
        goal="verify reflected xss",
        risk="active",
    )
    store, artifact_store = _stores(tmp_path, task)
    artifact = artifact_store.save_text(
        task_id=task.id,
        intent_id=intent.id,
        kind="http_response",
        text="HTTP response contains Reflected payload",
        tool="fake",
        target="http://127.0.0.1:9000",
    )
    finding = Finding(
        id="finding_xss",
        task_id=task.id,
        title="Reflected XSS",
        target="http://127.0.0.1:9000",
        severity="medium",
        evidence_artifact_id=artifact.id,
        evidence_excerpt="Reflected payload",
    )
    result = WorkerResult(
        task_id=task.id,
        intent_id=intent.id,
        status="ok",
        artifacts=[artifact],
        findings=[finding],
    )

    scheduler = Scheduler(
        store=store,
        worker=StaticWorker(result),
        run_root=str(tmp_path / "runs"),
    )
    scheduler.run_intent(task=task, intent=intent)

    snapshot = store.task_snapshot(task.id)
    assert snapshot["findings"][0]["status"] == "candidate"
    assert any(
        event["type"] == "GATE_REJECTED"
        and event["payload"]["kind"] == "finding"
        for event in snapshot["events"]
    )
