"""Authoritative Host capability registry and profiles."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tga.domain.capabilities import HostCapabilityDefinition, HostCapabilityProfile


ROLES = ("supervisor", "worker", "reviewer", "reporter")
ALL_ROLES = ROLES
SUPERVISOR_WORKER = ("supervisor", "worker")
WORKER_ONLY = ("worker",)


def _schema(
    properties: dict[str, Any] | None = None, required: tuple[str, ...] = ()
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties or {},
    }
    if required:
        value["required"] = list(required)
    return value


def _definition(
    capability_id: str,
    display_name: str,
    category: str,
    description: str,
    roles: tuple[str, ...],
    handler_key: str,
    *,
    risk: str = "passive",
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
) -> HostCapabilityDefinition:
    return HostCapabilityDefinition(
        id=capability_id,
        display_name=display_name,
        category=category,
        description=description,
        allowed_roles=roles,
        risk=risk,
        input_schema=_schema(properties, required),
        output_schema=_schema(),
        handler_key=handler_key,
    )


def default_host_capabilities() -> tuple[HostCapabilityDefinition, ...]:
    generic = {"id": {"type": "string"}}
    string_list = {"type": "array", "items": {"type": "string"}, "maxItems": 1024}
    intent_properties = {
        "kind": {"type": "string"},
        "title": {"type": "string"},
        "objective": {"type": "string"},
        "allowed_resource_ids": string_list,
        "relevant_knowledge_ids": string_list,
        "relevant_evidence_claim_ids": string_list,
        "priority": {"type": "integer"},
    }
    values = (
        _definition("inspect_task_state", "Inspect task state", "orchestration", "Read current durable orchestration state.", ("supervisor",), "orchestration.inspect_task_state"),
        _definition("create_intent", "Create intent", "orchestration", "Create a task-scoped work intent.", ("supervisor",), "orchestration.create_intent", risk="active", properties=intent_properties, required=("kind", "title", "objective")),
        _definition("update_global_plan", "Update global plan", "orchestration", "Update the global plan through compare-and-swap.", ("supervisor",), "orchestration.update_global_plan", risk="active", properties={"action": {"type": "string", "enum": ["create_intent"]}, **intent_properties}, required=("action", "kind", "title", "objective")),
        _definition("spawn_solver", "Spawn solver", "orchestration", "Assign an eligible Solver to an intent.", ("supervisor",), "orchestration.spawn_solver", risk="active"),
        _definition("inspect_worker_result", "Inspect worker result", "result", "Read a submitted Worker result.", ("supervisor",), "result.inspect_worker_result", properties={"worker_result_id": {"type": "string"}}),
        _definition("request_review", "Request review", "review", "Request evidence review.", ("supervisor",), "review.request_review", risk="active"),
        _definition("request_report", "Request report", "reporting", "Request report production.", ("supervisor",), "reporting.request_report", risk="active"),
        _definition("confirm_finding", "Confirm finding", "review", "Confirm a reviewed finding.", ("supervisor",), "evidence.confirm_finding", risk="active", properties={"finding_id": {"type": "string"}}, required=("finding_id",)),
        _definition("propose_task_completion", "Propose task completion", "result", "Submit the task to host completion validation.", ("supervisor",), "result.propose_task_completion", risk="active", properties={"summary": {"type": "string"}, "flag": {"type": "string"}, "evidence_artifact_ids": string_list, "limitations": string_list}, required=("summary",)),
        _definition("update_local_plan", "Update local plan", "orchestration", "Update this Solver's local plan.", WORKER_ONLY, "orchestration.update_local_plan", risk="active", properties={"summary": {"type": "string"}, "completed_step_ids": string_list, "blocked_step_ids": string_list}),
        _definition("propose_knowledge", "Propose knowledge", "knowledge", "Submit candidate task knowledge.", WORKER_ONLY, "knowledge.propose", risk="active", properties={"summary": {"type": "string"}, "claims": string_list, "artifact_ids": string_list}),
        _definition("submit_worker_result", "Submit worker result", "result", "Submit the assigned Worker's structured result.", WORKER_ONLY, "result.submit_worker_result", risk="active", properties={"status": {"type": "string", "enum": ["succeeded", "partial", "blocked", "failed"]}, "summary": {"type": "string"}, "artifact_ids": string_list, "candidate_evidence_claim_ids": string_list, "candidate_knowledge_ids": string_list, "finding_ids": string_list, "coverage": {"type": "object", "additionalProperties": False, "properties": {"completed": string_list, "not_covered": string_list}}, "limitations": string_list}, required=("status", "summary")),
        _definition("input.list", "List inputs", "task_input", "List immutable task inputs.", SUPERVISOR_WORKER, "task_input.list"),
        _definition("input.get", "Get input metadata", "task_input", "Read immutable task input metadata.", SUPERVISOR_WORKER, "task_input.get", properties={"input_id": {"type": "string"}}, required=("input_id",)),
        _definition("input.read", "Read input", "task_input", "Read a bounded segment of an immutable task input.", SUPERVISOR_WORKER, "task_input.read", properties={"input_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 262144}}, required=("input_id",)),
        _definition("input.search", "Search input", "task_input", "Search text in an immutable task input.", SUPERVISOR_WORKER, "task_input.search", properties={"input_id": {"type": "string"}, "query": {"type": "string"}}, required=("input_id", "query")),
        _definition("input.view", "View input", "task_input", "View an immutable image input.", SUPERVISOR_WORKER, "task_input.view", properties={"input_id": {"type": "string"}}, required=("input_id",)),
        _definition("input.materialize", "Materialize input", "task_input", "Mount an immutable task input in the Solver workspace.", WORKER_ONLY, "task_input.materialize", risk="active", properties={"input_id": {"type": "string"}}, required=("input_id",)),
        _definition("artifact.list", "List artifacts", "artifact", "List task-scoped immutable Artifacts.", ALL_ROLES, "artifact.list"),
        _definition("artifact.inspect", "Inspect artifact", "artifact", "Read a bounded view of a task-scoped immutable Artifact.", ALL_ROLES, "artifact.inspect", properties={"artifact_id": {"type": "string"}, "query": {"type": "string"}, "section": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 262144}}, required=("artifact_id",)),
        _definition("artifact.publish", "Publish artifact", "artifact", "Persist a Solver Kali workspace file as an immutable Artifact.", WORKER_ONLY, "artifact.publish", risk="active", properties={"relative_path": {"type": "string"}, "media_type": {"type": "string"}, "label": {"type": "string"}}, required=("relative_path",)),
        _definition("retrieval.search", "Search retrieval index", "retrieval", "Search an authorized immutable index snapshot.", SUPERVISOR_WORKER, "retrieval.search", properties={"query": {"type": "string"}}, required=("query",)),
        _definition("knowledge.inspect", "Inspect knowledge", "knowledge", "Read task-scoped knowledge.", ("supervisor", "worker", "reviewer"), "knowledge.inspect", properties=generic),
        _definition("evidence.inspect", "Inspect evidence", "evidence", "Read task-scoped evidence claims.", ("supervisor", "worker", "reviewer"), "evidence.inspect", properties=generic),
        _definition("review_evidence", "Review evidence", "review", "Record an evidence review decision.", ("reviewer",), "review.evidence", risk="active", properties={"status": {"type": "string", "enum": ["confirmed", "rejected", "needs_more_evidence"]}, "confirmed_evidence_claim_ids": string_list, "confirmed_knowledge_ids": string_list, "confirmed_finding_ids": string_list, "rejected_ids": string_list, "contradictions": string_list}),
        _definition("review_finding", "Review finding", "review", "Record a finding review decision.", ("reviewer",), "review.finding", risk="active", properties={"status": {"type": "string", "enum": ["confirmed", "rejected", "needs_more_evidence"]}, "confirmed_evidence_claim_ids": string_list, "confirmed_knowledge_ids": string_list, "confirmed_finding_ids": string_list, "rejected_ids": string_list, "contradictions": string_list}),
        _definition("request_more_evidence", "Request more evidence", "review", "Request additional evidence from the supervisor.", ("reviewer",), "review.request_more_evidence", risk="active", properties={"contradictions": string_list}),
        _definition("confirmed_evidence.read", "Read confirmed evidence", "evidence", "Read confirmed evidence for reporting.", ("reporter",), "reporting.confirmed_evidence"),
        _definition("confirmed_knowledge.read", "Read confirmed knowledge", "knowledge", "Read confirmed knowledge for reporting.", ("reporter",), "reporting.confirmed_knowledge"),
        _definition("confirmed_findings.read", "Read confirmed findings", "evidence", "Read confirmed findings for reporting.", ("reporter",), "reporting.confirmed_findings"),
        _definition("report.write", "Write report", "reporting", "Write the evidence-backed task report.", ("reporter",), "reporting.write", risk="active", properties={"summary": {"type": "string"}, "report_artifact_id": {"type": "string"}, "limitations": string_list}, required=("summary",)),
    )
    return values


DEFAULT_HOST_PROFILES = (
    HostCapabilityProfile(id="supervisor-default", capability_ids=(
        "inspect_task_state", "create_intent", "update_global_plan", "spawn_solver",
        "inspect_worker_result", "request_review", "request_report", "confirm_finding",
        "propose_task_completion", "input.list", "input.get", "input.read",
        "input.search", "input.view", "artifact.list", "artifact.inspect",
        "retrieval.search", "knowledge.inspect", "evidence.inspect",
    )),
    HostCapabilityProfile(id="worker-default", capability_ids=(
        "update_local_plan", "propose_knowledge", "submit_worker_result",
        "input.list", "input.get", "input.read", "input.search", "input.view",
        "input.materialize", "artifact.list", "artifact.inspect", "artifact.publish",
        "retrieval.search", "knowledge.inspect", "evidence.inspect",
    )),
    HostCapabilityProfile(id="reviewer-default", capability_ids=(
        "artifact.list", "artifact.inspect", "evidence.inspect", "knowledge.inspect",
        "review_evidence", "review_finding", "request_more_evidence",
    )),
    HostCapabilityProfile(id="reporter-default", capability_ids=(
        "confirmed_evidence.read", "confirmed_knowledge.read", "confirmed_findings.read",
        "artifact.inspect", "report.write",
    )),
)


class HostCapabilityRegistry:
    def __init__(
        self,
        definitions: Iterable[HostCapabilityDefinition] | None = None,
        profiles: Iterable[HostCapabilityProfile] | None = None,
    ) -> None:
        self._definitions = {item.id: item for item in (definitions or default_host_capabilities())}
        self._profiles = {item.id: item for item in (profiles or DEFAULT_HOST_PROFILES)}
        if len(self._definitions) != len(tuple(definitions or default_host_capabilities())):
            raise ValueError("duplicate Host capability id")
        self.validate_profiles()

    def all(self) -> tuple[HostCapabilityDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def require(self, capability_id: str) -> HostCapabilityDefinition:
        try:
            return self._definitions[capability_id]
        except KeyError as exc:
            raise KeyError(f"unknown Host capability: {capability_id}") from exc

    def profiles(self) -> tuple[HostCapabilityProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    def require_profile(self, profile_id: str) -> HostCapabilityProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise KeyError(f"unknown Host capability profile: {profile_id}") from exc

    def validate_profiles(self) -> None:
        for profile in self._profiles.values():
            missing = set(profile.capability_ids) - set(self._definitions)
            if missing:
                raise ValueError(f"Host profile {profile.id} references unknown capabilities: {sorted(missing)}")

    def validate_handlers(self, handler_keys: Iterable[str]) -> None:
        available = set(handler_keys)
        missing = [item.id for item in self.all() if item.handler_key not in available]
        if missing:
            raise RuntimeError(f"Host capabilities missing handlers: {missing}")


__all__ = ["DEFAULT_HOST_PROFILES", "HostCapabilityRegistry", "default_host_capabilities"]
