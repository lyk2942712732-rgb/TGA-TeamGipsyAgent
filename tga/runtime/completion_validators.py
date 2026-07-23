"""Mode-specific, evidence-backed finish_session validation."""

from __future__ import annotations

from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from tga.contracts import CtfModeConfig, ReverseAnalysisModeConfig, TGATask, ArtifactRecord, VulnerabilityResearchModeConfig
from tga.core.flag_gate import is_placeholder_flag
from tga.evidence.store import EvidenceStore
from tga.modes import TaskMode
from tga.runtime.completion import CompletionGate


ClaimKind = Literal[
    "conclusion", "finding", "vulnerability", "ioc", "attack_path",
    "root_cause", "impact", "reproduction", "recovered_result",
]


class CompletionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=1200)
    kind: ClaimKind = "conclusion"
    evidence_artifact_ids: list[str] = Field(default_factory=list, max_length=32)


class FinishSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=5000)
    evidence_artifact_ids: list[str] = Field(default_factory=list, max_length=64)
    claims: list[CompletionClaim] = Field(default_factory=list, max_length=32)
    coverage: list[str] = Field(default_factory=list, max_length=32)
    limitations: list[str] = Field(default_factory=list, max_length=32)
    flag: str | None = Field(default=None, max_length=500)


class CompletionValidationResult(BaseModel):
    accepted: bool
    code: str
    message: str
    missing: list[str] = Field(default_factory=list)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    retryable: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class CompletionValidator(Protocol):
    def validate(self, *, context: "CompletionValidationContext", submission: FinishSubmission) -> CompletionValidationResult: ...


class CompletionValidationContext:
    def __init__(
        self, *, task: TGATask, solver_id: str, store: EvidenceStore, artifact_text,
        remote_flag_verifier: Callable[[TGATask, str], bool] | None = None,
    ) -> None:
        self.task = task
        self.solver_id = solver_id
        self.store = store
        self.artifact_text = artifact_text
        self.remote_flag_verifier = remote_flag_verifier

    def resolve_artifacts(self, submission: FinishSubmission) -> tuple[list[ArtifactRecord], list[str]]:
        ids = list(dict.fromkeys([
            *submission.evidence_artifact_ids,
            *(artifact_id for claim in submission.claims for artifact_id in claim.evidence_artifact_ids),
        ]))
        artifacts: list[ArtifactRecord] = []
        invalid: list[str] = []
        for artifact_id in ids:
            artifact = self.store.get_artifact(artifact_id)
            if artifact is None or artifact.task_id != self.task.id:
                invalid.append(artifact_id)
            else:
                artifacts.append(artifact)
        return artifacts, invalid


class BaseCompletionValidator:
    def common(self, *, context: CompletionValidationContext, submission: FinishSubmission) -> tuple[list[ArtifactRecord], CompletionValidationResult | None]:
        artifacts, invalid = context.resolve_artifacts(submission)
        if context.task.mode != "ctf" and submission.flag is not None:
            return artifacts, rejected(
                "FLAG_NOT_ALLOWED_FOR_MODE",
                "The flag field is available only in CTF mode.",
                ["remove flag from finish_session"], artifacts,
            )
        if invalid:
            return artifacts, rejected(
                "INVALID_EVIDENCE_REFERENCE",
                "One or more Artifact IDs do not exist or belong to another task.",
                [f"valid task-owned Artifact: {artifact_id}" for artifact_id in invalid],
                artifacts,
                details={"invalid_artifact_ids": invalid},
            )
        return artifacts, None


class CTFCompletionValidator(BaseCompletionValidator):
    def validate(self, *, context: CompletionValidationContext, submission: FinishSubmission) -> CompletionValidationResult:
        artifacts, failure = self.common(context=context, submission=submission)
        if failure:
            return failure
        flag = (submission.flag or "").strip()
        config = context.task.mode_config if isinstance(context.task.mode_config, CtfModeConfig) else CtfModeConfig(flag_format=context.task.flag_format)
        if not flag:
            if config.alternative_proof and artifacts and submission.claims:
                return accepted("CTF_ALTERNATIVE_PROOF_VERIFIED", "Configured non-flag completion proof is Artifact-backed.", [item.id for item in artifacts], details={"alternative_proof": config.alternative_proof})
            return rejected("CTF_FLAG_REQUIRED", "CTF completion requires a candidate flag.", ["flag"], artifacts)
        if not artifacts:
            return rejected("CTF_FLAG_EVIDENCE_REQUIRED", "The flag must be cited from a task-owned Artifact.", ["evidence_artifact_ids"], artifacts)
        gate = CompletionGate(context.store, artifact_text=context.artifact_text)
        if config.verifier.kind != "local_regex" and context.remote_flag_verifier is None:
            return rejected("CTF_VERIFIER_UNAVAILABLE", "The configured platform/MCP verifier is unavailable.", [f"configured {config.verifier.kind} verifier"], artifacts)
        if context.remote_flag_verifier is not None:
            evidence = next((item for item in artifacts if flag in context.artifact_text(context.task.id, item)), None)
            if evidence is None or is_placeholder_flag(flag):
                return rejected("CTF_FLAG_NOT_VERIFIED", "Remote verification still requires non-placeholder task-owned Artifact provenance.", ["valid Artifact-backed flag"], artifacts)
            try:
                remotely_accepted = bool(context.remote_flag_verifier(context.task, flag))
            except Exception as exc:
                return rejected(
                    "CTF_REMOTE_VERIFIER_ERROR", "The configured flag verifier failed.",
                    ["successful remote verification"], artifacts,
                    details={"error_type": type(exc).__name__},
                )
            if not remotely_accepted:
                return rejected("CTF_REMOTE_FLAG_REJECTED", "The configured platform verifier rejected the flag.", ["platform-accepted flag"], artifacts)
            decision = gate.confirm(
                task=context.task, candidate=flag, evidence=evidence,
                solver_id=context.solver_id, reason="remote_verifier_accepted",
            )
        else:
            decision = gate.evaluate(
                task=context.task, candidate=flag, artifacts=artifacts, solver_id=context.solver_id,
            )
        if not decision.solved:
            return rejected(
                "CTF_FLAG_NOT_VERIFIED",
                "Flag format, placeholder, Artifact content, or provenance validation failed.",
                ["valid Artifact-backed flag"], artifacts,
            )
        expected = config.expected_flag_count or 1
        confirmed_count = len(context.store.task_snapshot(context.task.id).get("flags") or [])
        if confirmed_count < expected:
            return rejected(
                "CTF_EXPECTED_FLAGS_MISSING", "Not all expected flags have been verified.",
                [f"{expected - confirmed_count} additional verified flag(s)"], artifacts,
                details={"expected_flag_count": expected, "confirmed_flag_count": confirmed_count},
            )
        return accepted("CTF_FLAG_VERIFIED", "Artifact-backed flag verified.", [decision.evidence_artifact_id] if decision.evidence_artifact_id else [])


class PenetrationTestCompletionValidator(BaseCompletionValidator):
    def validate(self, *, context: CompletionValidationContext, submission: FinishSubmission) -> CompletionValidationResult:
        artifacts, failure = self.common(context=context, submission=submission)
        if failure:
            return failure
        missing = _base_non_ctf_missing(submission, artifacts, require_limitations=True)
        unsupported = _claims_without_evidence(submission, {"finding", "vulnerability", "impact", "attack_path"})
        if unsupported:
            missing.append("evidence for claimed findings/impact: " + ", ".join(unsupported))
        confirmed_without_evidence = [
            str(item.get("id")) for item in (context.store.task_snapshot(context.task.id).get("findings") or [])
            if item.get("status") == "confirmed" and not item.get("evidence_artifact_id")
        ]
        if confirmed_without_evidence:
            missing.append("evidence for confirmed Findings")
        return _finish_or_reject("PENETRATION_TEST_COMPLETE", submission, artifacts, missing, {"unsupported_findings": unsupported})


class IncidentResponseCompletionValidator(BaseCompletionValidator):
    def validate(self, *, context: CompletionValidationContext, submission: FinishSubmission) -> CompletionValidationResult:
        artifacts, failure = self.common(context=context, submission=submission)
        if failure:
            return failure
        missing = _base_non_ctf_missing(submission, artifacts)
        if not submission.claims:
            missing.append("at least one evidence-backed investigation conclusion")
        unsupported = _claims_without_evidence(submission, {"conclusion", "ioc", "attack_path", "root_cause", "impact"})
        if unsupported:
            missing.append("evidence for IOC/root-cause/impact claims: " + ", ".join(unsupported))
        return _finish_or_reject("INCIDENT_RESPONSE_COMPLETE", submission, artifacts, missing, {"unsupported_investigation_claims": unsupported})


class VulnerabilityResearchCompletionValidator(BaseCompletionValidator):
    def validate(self, *, context: CompletionValidationContext, submission: FinishSubmission) -> CompletionValidationResult:
        artifacts, failure = self.common(context=context, submission=submission)
        if failure:
            return failure
        has_vulnerability = any(claim.kind in {"finding", "vulnerability"} for claim in submission.claims)
        missing = _base_non_ctf_missing(submission, artifacts, require_limitations=not has_vulnerability)
        unsupported = _claims_without_evidence(submission, {"finding", "vulnerability", "reproduction", "root_cause", "impact"})
        if unsupported:
            missing.append("reproduction Artifact for vulnerability claims: " + ", ".join(unsupported))
        kinds = {claim.kind for claim in submission.claims}
        if has_vulnerability and "root_cause" not in kinds:
            missing.append("root_cause claim")
        if has_vulnerability and "impact" not in kinds:
            missing.append("impact or exploit-precondition claim")
        config = context.task.mode_config
        if isinstance(config, VulnerabilityResearchModeConfig):
            if config.require_poc and "reproduction" not in kinds:
                missing.append("PoC/reproduction claim required by mode_config")
            if config.require_minimized_crash and not any("minimiz" in claim.statement.casefold() or "最小" in claim.statement for claim in submission.claims):
                missing.append("minimized crash sample evidence required by mode_config")
        return _finish_or_reject("VULNERABILITY_RESEARCH_COMPLETE", submission, artifacts, missing, {"vulnerability_claimed": has_vulnerability})


class ReverseEngineeringCompletionValidator(BaseCompletionValidator):
    def validate(self, *, context: CompletionValidationContext, submission: FinishSubmission) -> CompletionValidationResult:
        artifacts, failure = self.common(context=context, submission=submission)
        if failure:
            return failure
        missing = _base_non_ctf_missing(submission, artifacts)
        if not any(claim.kind in {"recovered_result", "conclusion"} for claim in submission.claims):
            missing.append("recovered_result or conclusion claim")
        unsupported = _claims_without_evidence(submission, {"recovered_result", "conclusion"})
        if unsupported:
            missing.append("analysis Artifact for recovered results: " + ", ".join(unsupported))
        config = context.task.mode_config
        if isinstance(config, ReverseAnalysisModeConfig) and config.expected_outputs:
            summary = " ".join([submission.summary, *(claim.statement for claim in submission.claims)]).casefold()
            absent = [item for item in config.expected_outputs if item.casefold() not in summary]
            if absent:
                missing.append("requested reverse-analysis outputs: " + ", ".join(absent))
        return _finish_or_reject("REVERSE_ENGINEERING_COMPLETE", submission, artifacts, missing, {"unsupported_results": unsupported})


VALIDATORS: dict[TaskMode, CompletionValidator] = {
    "ctf": CTFCompletionValidator(),
    "penetration_test": PenetrationTestCompletionValidator(),
    "incident_response": IncidentResponseCompletionValidator(),
    "vulnerability_research": VulnerabilityResearchCompletionValidator(),
    "reverse_engineering": ReverseEngineeringCompletionValidator(),
}


def validator_for(mode: TaskMode) -> CompletionValidator:
    return VALIDATORS[mode]


def finish_tool_schema(mode: TaskMode) -> dict[str, Any]:
    schema = FinishSubmission.model_json_schema()
    schema["additionalProperties"] = False
    if mode != "ctf":
        schema.get("properties", {}).pop("flag", None)
    return schema


def _base_non_ctf_missing(submission: FinishSubmission, artifacts: list[ArtifactRecord], *, require_limitations: bool = False) -> list[str]:
    missing: list[str] = []
    if not artifacts:
        missing.append("at least one task-owned evidence Artifact")
    if not submission.coverage:
        missing.append("coverage")
    if require_limitations and not submission.limitations:
        missing.append("limitations")
    return missing


def _claims_without_evidence(submission: FinishSubmission, kinds: set[str]) -> list[str]:
    return [claim.statement[:120] for claim in submission.claims if claim.kind in kinds and not claim.evidence_artifact_ids]


def _finish_or_reject(code: str, submission: FinishSubmission, artifacts: list[ArtifactRecord], missing: list[str], details: dict[str, Any]) -> CompletionValidationResult:
    if missing:
        return rejected(f"{code}_MISSING", "Completion declaration needs more evidence or coverage.", missing, artifacts, details=details)
    return accepted(code, "Mode-specific completion requirements satisfied.", [artifact.id for artifact in artifacts], details=details)


def accepted(code: str, message: str, evidence: list[str], *, details: dict[str, Any] | None = None) -> CompletionValidationResult:
    return CompletionValidationResult(accepted=True, code=code, message=message, evidence_artifact_ids=evidence, retryable=False, details=details or {})


def rejected(code: str, message: str, missing: list[str], artifacts: list[ArtifactRecord], *, details: dict[str, Any] | None = None) -> CompletionValidationResult:
    return CompletionValidationResult(accepted=False, code=code, message=message, missing=missing, evidence_artifact_ids=[item.id for item in artifacts], retryable=True, details=details or {})
