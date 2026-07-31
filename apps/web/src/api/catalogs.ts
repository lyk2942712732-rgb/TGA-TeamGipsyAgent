import { requestJson } from "./client";
import type { BackendCapabilityState } from "./capability-state";

type JsonObject = Record<string, unknown>;
type CatalogEnvelope = {
  supported: boolean;
  reason: string | null;
  items: JsonObject[];
  total: number;
};

export type CatalogResult<T> = {
  capability: BackendCapabilityState;
  reason: string | null;
  items: T[];
  total: number;
};

export type ArtifactViewModel = {
  id: string; taskId: string; name: string; type: string; sourceSolver: string;
  sourceIntent: string; media: string; hash: string; createdAt: string; status: string;
};
export type EvidenceClaimViewModel = {
  id: string; taskId: string; statement: string; artifact: string; locator: string;
  status: string; creator: string; reviewer: string; createdAt: string;
};
export type FindingViewModel = {
  id: string; taskId: string; title: string; severity: string; target: string;
  status: string; evidenceCount: number; creator: string; createdAt: string;
};
export type KnowledgeResourceViewModel = {
  id: string; taskId: string; type: string; scope: string; target: string;
  status: string; sourceSolver: string; createdAt: string;
};
export type ReportViewModel = {
  id: string; taskId: string; name: string; mode: string; version: string;
  status: string; findingCount: number; generatedAt: string; updatedAt: string;
};
export type KnowledgeBaseViewModel = {
  id: string; name: string; type: string; scope: string; documentCount: number;
  sourceCount: number; indexVersion: string; status: string; lastSyncAt: string;
};
export type TeamTemplateViewModel = {
  id: string; name: string; mode: string; supervisor: string; defaultRoles: string[];
  maxParallel: number; maxSolvers: number; status: string; updatedAt: string;
  requiredWorkers: string[]; availableWorkers: string[]; reviewer: string; reporter: string;
  spawnRules: string[]; completionPolicy: string; policySummary: string; version: string;
};
export type SolverDefinitionViewModel = {
  id: string; name: string; version: string; role: string; modes: string[];
  specialties: string[]; status: string; completionAuthority: string; toolPolicy: string;
  budgetSummary: string; instructions: string; capabilities: string[]; toolGroups: string[];
  skillTags: string[]; requiredSkills: string[]; intentKinds: string[]; outputContract: string;
  contentHash: string;
};
export type PolicyProfileViewModel = {
  id: string; name: string; mode: string; status: string; description: string;
  networkAccess: string; denyPrivate: string; denyLoopback: string; denyLinkLocal: string;
  denyMetadata: string; rateLimit: string; concurrency: string; timeout: string;
  localCompute: string; highImpact: string; maxRuntime: string; tokenLimit: string;
  toolCallLimit: string; artifactLimit: string;
};

const text = (value: unknown, fallback = "-") => typeof value === "string" && value.trim() ? value : fallback;
const number = (value: unknown, fallback = 0) => typeof value === "number" && Number.isFinite(value) ? value : fallback;
const object = (value: unknown): JsonObject => value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
const booleanLabel = (value: unknown) => value === true ? "是" : value === false ? "否" : "未声明";
const compact = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(compact).filter(Boolean).join("、") || "-";
  if (value && typeof value === "object") return Object.entries(value as JsonObject).map(([key, item]) => `${key}: ${compact(item)}`).join(" · ") || "-";
  return "-";
};

async function catalog<T>(kind: string, adapt: (item: JsonObject) => T): Promise<CatalogResult<T>> {
  const payload = await requestJson<CatalogEnvelope>(`/api/v2/catalog/${kind}?limit=200`);
  return {
    capability: payload.supported ? "read_only" : "unsupported",
    reason: payload.reason,
    items: payload.items.map(adapt),
    total: payload.total,
  };
}

export type ResourceCatalogViewModel = {
  capability: BackendCapabilityState; reason: string | null; total: number;
  artifacts: ArtifactViewModel[]; evidence: EvidenceClaimViewModel[];
  findings: FindingViewModel[]; knowledge: KnowledgeResourceViewModel[];
};

export async function fetchResourceCatalog(): Promise<ResourceCatalogViewModel> {
  const result = await catalog("resources", (item) => item);
  const groups = { artifacts: [] as ArtifactViewModel[], evidence: [] as EvidenceClaimViewModel[], findings: [] as FindingViewModel[], knowledge: [] as KnowledgeResourceViewModel[] };
  result.items.forEach((row) => {
    const raw = object(row.raw);
    const kind = text(row.kind, "artifacts");
    const base = { id: text(row.id), taskId: text(row.task_id), createdAt: text(raw.created_at), status: text(row.status ?? raw.status, "available") };
    if (kind === "artifacts") groups.artifacts.push({ ...base, name: text(row.title), type: text(raw.kind ?? raw.type), sourceSolver: text(raw.solver_id ?? raw.created_by_solver_id), sourceIntent: text(raw.intent_id), media: text(raw.media_type ?? raw.size), hash: text(raw.sha256) });
    if (kind === "evidence") groups.evidence.push({ ...base, statement: text(row.title ?? raw.statement), artifact: text(raw.artifact_id), locator: compact(raw.locator), creator: text(raw.created_by_solver_id), reviewer: text(raw.reviewed_by_solver_id) });
    if (kind === "findings") groups.findings.push({ ...base, title: text(row.title), severity: text(raw.severity, "unknown"), target: text(raw.target), evidenceCount: strings(raw.evidence_claim_ids).length, creator: text(raw.created_by_solver_id) });
    if (kind === "knowledge") groups.knowledge.push({ ...base, type: text(raw.kind ?? raw.type), scope: text(raw.scope), target: text(row.title ?? raw.subject), sourceSolver: text(raw.created_by_solver_id) });
  });
  return { capability: result.capability, reason: result.reason, total: result.total, ...groups };
}

export const fetchReportsCatalog = () => catalog<ReportViewModel>("reports", (row) => ({
  id: text(row.id), taskId: text(row.task_id), name: text(row.title, "任务报告"), mode: text(row.mode),
  version: text(row.version, "v1"), status: text(row.status, "exported"), findingCount: number(row.finding_count),
  generatedAt: text(row.created_at ?? row.updated_at), updatedAt: text(row.updated_at),
}));

export const fetchKnowledgeBasesCatalog = () => catalog<KnowledgeBaseViewModel>("knowledge-bases", (row) => ({
  id: text(row.id), name: text(row.name ?? row.title ?? row.id), type: text(row.kind ?? row.type, "Vector"),
  scope: text(row.scope), documentCount: number(row.document_count ?? row.documents), sourceCount: number(row.source_count ?? row.sources),
  indexVersion: text(row.index_version ?? row.snapshot_id), status: text(row.status, "available"), lastSyncAt: text(row.last_sync_at ?? row.updated_at),
}));

export const fetchTeamTemplatesCatalog = () => catalog<TeamTemplateViewModel>("teams", (row) => {
  const workers = object(row.workers);
  const limits = object(row.limits);
  return {
    id: text(row.id ?? row.name), name: text(row.name ?? row.id), mode: strings(row.modes).join(" / ") || text(row.mode),
    supervisor: text(row.supervisor ?? row.supervisor_definition_id), defaultRoles: strings(row.default_roles ?? row.roles),
    maxParallel: number(row.max_parallel_solvers ?? limits.max_active_workers, 1), maxSolvers: number(row.max_total_solvers ?? limits.max_total_solvers, 1),
    status: text(row.status, "enabled"), updatedAt: text(row.updated_at), requiredWorkers: strings(row.required_workers ?? row.required_solver_definition_ids ?? workers.required),
    availableWorkers: strings(row.available_workers ?? row.available_solver_definition_ids ?? workers.available), reviewer: text(row.reviewer ?? row.reviewer_definition_id), reporter: text(row.reporter ?? row.reporter_definition_id),
    spawnRules: strings(row.spawn_rules).length ? strings(row.spawn_rules) : strings(row.spawn_rules).concat(), completionPolicy: compact(row.completion_policy), policySummary: compact(row.default_execution_policy ?? row.execution_policy),
    version: text(row.content_sha256 ?? row.version),
  };
});

export const fetchSolverDefinitionsCatalog = () => catalog<SolverDefinitionViewModel>("solvers", (row) => ({
  id: text(row.id ?? row.name), name: text(row.display_name ?? row.name ?? row.id), version: text(row.version), role: text(row.role ?? row.orchestration_role),
  modes: strings(row.modes ?? row.accepted_modes), specialties: strings(row.specialties ?? row.tags), status: text(row.status, "enabled"),
  completionAuthority: compact(row.completion_authority), toolPolicy: compact(row.tool_policy_profile ?? row.tool_policy),
  budgetSummary: compact(row.default_budget), instructions: text(row.system_prompt_template, "未提供 Instructions 模板"),
  capabilities: strings(row.required_capabilities), toolGroups: strings(row.allowed_tool_groups), skillTags: strings(row.default_skill_tags),
  requiredSkills: strings(row.required_skill_names), intentKinds: strings(row.accepted_intent_kinds), outputContract: compact(row.output_contract),
  contentHash: text(row.content_sha256),
}));

export const fetchPoliciesCatalog = () => catalog<PolicyProfileViewModel>("policies", (row) => {
  const network = object(row.network); const local = object(row.local_compute); const high = object(row.high_impact); const budget = object(row.budget ?? row.execution_budget);
  return {
    id: text(row.id), name: text(row.name ?? row.id, "Task Execution Policy"), mode: text(row.mode, "All modes"), status: text(row.status, "available"),
    description: text(row.description ?? row.source), networkAccess: text(network.access ?? row.network_access), denyPrivate: booleanLabel(network.deny_private_networks),
    denyLoopback: booleanLabel(network.deny_loopback), denyLinkLocal: booleanLabel(network.deny_link_local), denyMetadata: booleanLabel(network.deny_cloud_metadata),
    rateLimit: text(network.rate_limit_per_minute), concurrency: text(network.concurrency ?? budget.concurrency), timeout: text(network.request_timeout_seconds ?? budget.timeout_seconds),
    localCompute: text(local.mode), highImpact: text(high.mode), maxRuntime: text(budget.max_runtime_seconds), tokenLimit: text(budget.token_limit),
    toolCallLimit: text(budget.tool_call_limit), artifactLimit: text(budget.artifact_limit),
  };
});
