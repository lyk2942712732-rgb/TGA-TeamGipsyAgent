"""Simple sequential scheduler."""

from __future__ import annotations

from pathlib import Path

from tga.contracts import Intent, TGATask, WorkerResult
from tga.evidence.store import EvidenceStore
from tga.workers.base import Worker


class Scheduler:
    def __init__(self, *, store: EvidenceStore, worker: Worker, run_root: str):
        self.store = store
        self.worker = worker
        self.run_root = Path(run_root)

    def run_intent(self, *, task: TGATask, intent: Intent) -> WorkerResult:
        self.store.add_intent(intent)
        self.store.update_intent_status(intent.id, "running")
        workspace = self.run_root / task.id / "work" / intent.id
        result = self.worker.run(task=task, intent=intent, workspace=str(workspace))
        for artifact in result.artifacts:
            self.store.add_artifact(artifact)
        for finding in result.findings:
            self.store.add_candidate_finding(finding)
        self.store.update_intent_status(intent.id, "done" if result.status == "ok" else result.status)
        return result

