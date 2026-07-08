"""Simple sequential scheduler."""

from __future__ import annotations

from pathlib import Path

from tga.contracts import ArtifactRecord, Finding, Intent, TGATask, WorkerResult
from tga.core.evidence_gate import finding_ok
from tga.core.flag_gate import flag_ok
from tga.evidence.store import EvidenceStore
from tga.orchestrator.planner import explain_adaptation, explain_intent_execution
from tga.workers.base import Worker


class Scheduler:
    def __init__(self, *, store: EvidenceStore, worker: Worker, run_root: str):
        self.store = store
        self.worker = worker
        self.run_root = Path(run_root)

    def run_intent(self, *, task: TGATask, intent: Intent) -> WorkerResult:
        self.store.add_intent(intent)
        self.store.add_event(
            task.id,
            "DECISION_TRACE",
            explain_intent_execution(task, intent),
            intent_id=intent.id,
        )
        safety = self._safety_decision(task=task, intent=intent)
        self.store.add_event(task.id, "SAFETY_DECISION", safety, intent_id=intent.id)
        if not safety["allowed"]:
            self.store.update_intent_status(intent.id, "blocked")
            result = WorkerResult(
                task_id=task.id,
                intent_id=intent.id,
                status="blocked",
                errors=[safety["reason"]],
            )
            self.store.add_event(
                task.id,
                "INTENT_RESULT",
                _result_summary(result),
                intent_id=intent.id,
            )
            return result
        self.store.update_intent_status(intent.id, "running")
        workspace = self.run_root / task.id / "work" / intent.id
        result = self.worker.run(task=task, intent=intent, workspace=str(workspace))
        for artifact in result.artifacts:
            self.store.add_artifact(artifact)
        artifact_texts = {
            artifact.id: self._read_artifact_text(task=task, artifact=artifact)
            for artifact in result.artifacts
        }
        for flag in result.flags:
            self._gate_flag(
                task=task,
                intent=intent,
                flag=flag,
                artifacts=result.artifacts,
                artifact_texts=artifact_texts,
            )
        for finding in result.findings:
            self.store.add_candidate_finding(finding)
            self._gate_finding(
                task=task,
                intent=intent,
                finding=finding,
                artifact_texts=artifact_texts,
            )
        status = "done" if result.status == "ok" else result.status
        self.store.update_intent_status(intent.id, status)
        self.store.add_event(
            task.id,
            "INTENT_RESULT",
            _result_summary(result),
            intent_id=intent.id,
        )
        self.store.add_event(
            task.id,
            "ADAPTATION_DECISION",
            explain_adaptation(task, intent, status=status, errors=result.errors),
            intent_id=intent.id,
        )
        return result

    @staticmethod
    def _safety_decision(*, task: TGATask, intent: Intent) -> dict:
        if intent.risk == "destructive":
            return {
                "allowed": False,
                "reason": "destructive_intent_not_allowed",
                "rationale": "Week 1 MVP never executes destructive actions.",
            }
        if intent.risk == "active" and task.intensity == "passive":
            return {
                "allowed": False,
                "reason": "active_intent_blocked_by_passive_intensity",
                "rationale": "Passive intensity allows collection and reporting but blocks active verification.",
            }
        return {
            "allowed": True,
            "reason": "within_task_policy",
            "rationale": "Intent risk is compatible with task intensity and scope policy.",
        }

    def _gate_flag(
        self,
        *,
        task: TGATask,
        intent: Intent,
        flag: str,
        artifacts: list[ArtifactRecord],
        artifact_texts: dict[str, str],
    ) -> None:
        evidence_artifact = next(
            (
                artifact
                for artifact in artifacts
                if flag_ok(
                    flag,
                    flag_format=task.flag_format or "",
                    artifact_texts=[artifact_texts.get(artifact.id, "")],
                )
            ),
            None,
        )
        if evidence_artifact:
            self.store.add_flag(task.id, flag, evidence_artifact.id)
            self.store.add_event(
                task.id,
                "FLAG_CONFIRMED",
                {"value": flag, "evidence_artifact_id": evidence_artifact.id},
                intent_id=intent.id,
            )
            return
        self.store.add_event(
            task.id,
            "GATE_REJECTED",
            {
                "kind": "flag",
                "value": flag,
                "reason": "flag_format_or_provenance_failed",
            },
            intent_id=intent.id,
        )

    def _gate_finding(
        self,
        *,
        task: TGATask,
        intent: Intent,
        finding: Finding,
        artifact_texts: dict[str, str],
    ) -> None:
        artifact_text = (
            artifact_texts.get(finding.evidence_artifact_id or "")
            if finding.evidence_artifact_id
            else None
        )
        if finding_ok(finding, task=task, artifact_text=artifact_text):
            self.store.confirm_finding(finding.id, finding.evidence_artifact_id or "")
            self.store.add_event(
                task.id,
                "FINDING_CONFIRMED",
                {
                    "finding_id": finding.id,
                    "evidence_artifact_id": finding.evidence_artifact_id,
                },
                intent_id=intent.id,
            )
            return
        self.store.add_event(
            task.id,
            "GATE_REJECTED",
            {
                "kind": "finding",
                "finding_id": finding.id,
                "reason": "finding_evidence_gate_failed",
            },
            intent_id=intent.id,
        )

    def _read_artifact_text(self, *, task: TGATask, artifact: ArtifactRecord) -> str:
        relative_path = Path(artifact.path)
        if relative_path.is_absolute():
            return ""
        for base in (
            self.run_root / task.id / "artifacts",
            self.run_root / task.id,
        ):
            text = self._read_relative_text(base=base, relative_path=relative_path)
            if text:
                return text
        return ""

    @staticmethod
    def _read_relative_text(*, base: Path, relative_path: Path) -> str:
        try:
            base_resolved = base.resolve()
            path = (base_resolved / relative_path).resolve()
            path.relative_to(base_resolved)
            if not path.is_file():
                return ""
            return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return ""


def _result_summary(result: WorkerResult) -> dict:
    return {
        "status": result.status,
        "artifact_count": len(result.artifacts),
        "finding_count": len(result.findings),
        "flag_count": len(result.flags),
        "fact_count": len(result.facts),
        "lead_count": len(result.leads),
        "errors": result.errors,
    }

