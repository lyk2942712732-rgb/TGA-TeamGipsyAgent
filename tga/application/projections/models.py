"""Transport-stable Phase-9 read DTOs.

These models intentionally expose summaries instead of persistence rows or
complete domain objects.  Large content stays behind paginated/detail queries.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageMeta(ApiDTO):
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class EventEnvelope(ApiDTO):
    schema_version: int = Field(ge=1)
    id: str
    task_id: str
    seq: int = Field(ge=1)
    type: str
    solver_id: str | None = None
    intent_id: str | None = None
    payload: dict[str, Any]
    created_at: str


class EventPage(ApiDTO):
    schema_version: int = Field(default=6, ge=1)
    task_id: str
    after_seq: int = Field(default=0, ge=0)
    next_after_seq: int = Field(default=0, ge=0)
    latest_seq: int = Field(default=0, ge=0)
    has_more: bool = False
    events: list[EventEnvelope] = Field(default_factory=list, max_length=200)


class SessionAggregate(ApiDTO):
    status: str
    supervisor_solver_id: str | None = None
    active_solver_count: int = Field(default=0, ge=0)
    max_active_workers: int = Field(default=1, ge=1, le=2)
    task_budget_usage: dict[str, int] = Field(default_factory=dict)
    stop_reason: str | None = None
    timestamps: dict[str, str | None] = Field(default_factory=dict)
    # Compatibility counters remain summaries and are not used as Solver identity.
    turn_count: int = Field(default=0, ge=0)
    max_turns: int = Field(default=0, ge=0)


class TeamProjection(ApiDTO):
    task_id: str
    status: str
    supervisor_solver_id: str | None = None
    max_active_workers: int = Field(ge=1, le=2)
    max_total_solvers: int = Field(ge=1)
    active_solver_count: int = Field(default=0, ge=0)
    solver_ids: list[str] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    timestamps: dict[str, str | None] = Field(default_factory=dict)


class SolverProjection(ApiDTO):
    task_id: str
    solver_id: str
    definition_id: str
    orchestration_role: str
    specialties: list[str] = Field(default_factory=list)
    parent_solver_id: str | None = None
    assigned_intent_id: str | None = None
    status: str
    current_summary: str = ""
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    skill_snapshot: dict[str, Any] = Field(default_factory=dict)
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    budget_usage: dict[str, int] = Field(default_factory=dict)
    timestamps: dict[str, str | None] = Field(default_factory=dict)


class SolverRunProjection(ApiDTO):
    run_id: str
    task_id: str
    solver_id: str
    assignment_id: str | None = None
    intent_id: str | None = None
    orchestration_role: str
    state: str
    attempt: int = Field(ge=1)
    lease_owner: str | None = None
    fencing_token: int = Field(default=0, ge=0)
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    result_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


class SolverRunPage(PageMeta):
    schema_version: Literal[6] = 6
    task_id: str
    items: list[SolverRunProjection] = Field(default_factory=list)


class IntentProjection(ApiDTO):
    task_id: str
    intent_id: str
    kind: str
    title: str
    objective: str
    status: str
    assigned_solver_id: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    priority: int = 0
    budget: dict[str, int] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class WorkerResultProjection(ApiDTO):
    result_id: str
    solver_id: str
    intent_id: str
    status: str
    summary: str
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_claim_ids: list[str] = Field(default_factory=list)
    knowledge_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    budget_usage: dict[str, int] = Field(default_factory=dict)


class KnowledgeProjection(ApiDTO):
    knowledge_id: str
    scope: str
    target_id: str | None = None
    status: str
    kind: str
    content_preview: str
    content_sha256: str
    created_by_solver_id: str | None = None
    created_at: str


class ArtifactProjection(ApiDTO):
    artifact_id: str
    intent_id: str | None = None
    kind: str
    media_type: str | None = None
    tool: str | None = None
    target: str | None = None
    sha256: str
    created_at: str


class EvidenceClaimProjection(ApiDTO):
    claim_id: str
    statement_preview: str
    artifact_id: str
    locator: dict[str, Any]
    status: str
    created_by_solver_id: str | None = None
    reviewed_by_solver_id: str | None = None
    created_at: str
    reviewed_at: str | None = None


class FindingProjection(ApiDTO):
    finding_id: str
    title: str
    description_preview: str
    target: str | None = None
    severity: str
    status: str
    evidence_claim_ids: list[str] = Field(default_factory=list)
    created_by_solver_id: str | None = None
    created_at: str
    reviewed_at: str | None = None


class ActionProjection(ApiDTO):
    id: str
    action_id: str
    solver_id: str
    intent_id: str | None = None
    capability: str
    target: str
    risk: str
    effect: dict[str, Any] = Field(default_factory=dict)
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str
    summary: str = ""
    artifact_ids: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ApprovalProjection(ApiDTO):
    approval_id: str
    solver_id: str = ""
    intent_id: str | None = None
    action_id: str = ""
    action: dict[str, Any] = Field(default_factory=dict)
    risk: str = "passive"
    effect: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    alternatives: list[str] = Field(default_factory=list)
    deadline: str = ""
    status: str
    created_at: str = ""
    updated_at: str = ""


class RetrievalRunProjection(ApiDTO):
    retrieval_run_id: str
    owner_scope: str
    workspace_id: str | None = None
    task_id: str | None = None
    solver_id: str | None = None
    intent_id: str | None = None
    index_snapshot_id: str
    method: str
    query_preview: str
    hit_count: int = Field(default=0, ge=0)
    created_at: str


class RuntimeSnapshotResponse(ApiDTO):
    schema_version: Literal[6] = 6
    task: dict[str, Any]
    task_common_skill_snapshot: dict[str, Any] | None = None
    session: SessionAggregate
    team: TeamProjection
    solvers: list[SolverProjection] = Field(default_factory=list)
    solver_runs: list[SolverRunProjection] = Field(default_factory=list)
    intents: list[IntentProjection] = Field(default_factory=list)
    worker_results: list[WorkerResultProjection] = Field(default_factory=list)
    global_plan: dict[str, Any] | None = None
    knowledge: list[KnowledgeProjection] = Field(default_factory=list)
    artifacts: list[ArtifactProjection] = Field(default_factory=list)
    evidence_claims: list[EvidenceClaimProjection] = Field(default_factory=list)
    findings: list[FindingProjection] = Field(default_factory=list)
    actions: list[ActionProjection] = Field(default_factory=list)
    approvals: list[ApprovalProjection] = Field(default_factory=list)
    retrieval_runs: list[RetrievalRunProjection] = Field(default_factory=list)
    events: list[EventEnvelope] = Field(default_factory=list, max_length=100)
    events_page: dict[str, int | bool]
    latest_seq: int = Field(ge=0)
    challenge: dict[str, Any] = Field(default_factory=dict)
    flags: list[dict[str, Any]] = Field(default_factory=list)
    artifact_indexes: list[dict[str, Any]] = Field(default_factory=list)
    http_sessions: list[dict[str, Any]] = Field(default_factory=list)
    observer: dict[str, Any] = Field(default_factory=dict)
    context_metrics: list[dict[str, Any]] = Field(default_factory=list)


class SolverResponse(ApiDTO):
    schema_version: Literal[6] = 6
    task_id: str
    solver: SolverProjection


class TeamResponse(ApiDTO):
    schema_version: Literal[6] = 6
    task_id: str
    team: TeamProjection
    solvers: list[SolverProjection]


class IntentPage(PageMeta):
    schema_version: Literal[6] = 6
    task_id: str
    items: list[IntentProjection]


class ApprovalPage(PageMeta):
    schema_version: Literal[6] = 6
    task_id: str
    items: list[ApprovalProjection]


class EvidencePageResponse(ApiDTO):
    schema_version: Literal[6] = 6
    task_id: str
    artifacts: dict[str, Any]
    evidence_claims: dict[str, Any]
    findings: dict[str, Any]


class TaskDetailResponse(ApiDTO):
    """Lifecycle detail without the Runtime workbench projection."""

    schema_version: Literal[6] = 6
    task_id: str
    task: dict[str, Any]
    task_spec: dict[str, Any]
    lifecycle: dict[str, Any]
    input_summary: dict[str, Any]
    config_snapshot: dict[str, Any]


class OperationalTaskSummary(ApiDTO):
    task_id: str
    name: str
    mode: str
    status: str
    updated_at: str
    active_solvers: int = Field(default=0, ge=0)
    pending_approvals: int = Field(default=0, ge=0)
    intent_total: int = Field(default=0, ge=0)
    intent_completed: int = Field(default=0, ge=0)
    findings: int = Field(default=0, ge=0)
    artifacts: int = Field(default=0, ge=0)
    turn_count: int = Field(default=0, ge=0)
    max_turns: int = Field(default=0, ge=0)
    needs_attention: bool = False
    latest_event: dict[str, Any] | None = None


class DashboardAttentionItem(ApiDTO):
    id: str
    kind: Literal["approval", "user_input", "blocked"]
    task_id: str
    task_name: str
    title: str
    description: str
    status: str
    risk: str | None = None
    action_id: str | None = None
    updated_at: str


class SystemStatusSummary(ApiDTO):
    id: str
    label: str
    status: Literal["healthy", "available", "degraded", "unavailable"]
    detail: str
    available: bool


class DashboardMetrics(ApiDTO):
    running_tasks: int | None = Field(default=None, ge=0)
    pending_approvals: int | None = Field(default=None, ge=0)
    awaiting_user_input: int | None = Field(default=None, ge=0)
    blocked_tasks: int | None = Field(default=None, ge=0)
    active_solvers: int | None = Field(default=None, ge=0)


class DashboardResponse(ApiDTO):
    schema_version: Literal[1] = 1
    generated_at: str
    metrics: DashboardMetrics
    needs_attention: list[DashboardAttentionItem] = Field(default_factory=list, max_length=20)
    active_tasks: list[OperationalTaskSummary] = Field(default_factory=list, max_length=20)
    recent_completed: list[OperationalTaskSummary] = Field(default_factory=list, max_length=20)
    system_status: list[SystemStatusSummary] = Field(default_factory=list, max_length=20)
    unavailable_metrics: list[str] = Field(default_factory=list)


class GlobalApprovalItem(ApiDTO):
    approval_id: str
    task_id: str
    task_name: str
    solver_id: str
    intent_id: str | None = None
    action_id: str
    action_kind: str
    capability: str
    target: str
    risk: str
    effect: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    expected_outcome: str
    alternative_analysis: str
    alternatives: list[str] = Field(default_factory=list)
    reversibility: str
    expires_at: str | None = None
    status: Literal["pending", "approved", "rejected", "expired"]
    decision_allowed: bool
    decision_block_reason: str | None = None
    created_at: str
    updated_at: str


class GlobalApprovalFilters(ApiDTO):
    status: str | None = None
    task_id: str | None = None
    solver_id: str | None = None
    intent_id: str | None = None
    risk: str | None = None
    capability: str | None = None
    deadline: str | None = None


class GlobalApprovalPage(PageMeta):
    schema_version: Literal[1] = 1
    items: list[GlobalApprovalItem]
    filters: GlobalApprovalFilters = Field(default_factory=GlobalApprovalFilters)


class CatalogError(ApiDTO):
    code: Literal["CATALOG_DATABASE_INVALID", "CATALOG_RECORD_INVALID"]
    task_id: str
    message: str


class CatalogPage(PageMeta):
    schema_version: Literal[1] = 1
    kind: Literal[
        "resources", "reports", "knowledge-bases", "teams", "solvers",
        "policies", "skills",
    ]
    supported: bool = True
    reason: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    errors: list[CatalogError] = Field(default_factory=list, max_length=50)


# Compatibility names retained for earlier application callers.
class TaskSummaryProjection(ApiDTO):
    schema_version: int = 1
    task_id: str
    name: str
    mode: str
    goal: str
    status: str = "created"
    updated_at: str = ""


class SessionProjection(ApiDTO):
    schema_version: int = 1
    task_id: str
    status: str
    active_solver_id: str | None = None
    turn_count: int = 0
    max_turns: int = 0


class EvidenceProjection(ApiDTO):
    schema_version: int = 1
    task_id: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)


class TimelineProjection(ApiDTO):
    schema_version: int = 1
    task_id: str
    after_seq: int = 0
    next_after_seq: int = 0
    events: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [name for name in globals() if name.endswith(("Projection", "Response", "Page", "Envelope", "Aggregate"))]
