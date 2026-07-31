import { ApiError, apiBase, requestJson } from "./client";

export type CatalogAvailability = {
  supported: boolean;
  reason: string | null;
};

export type ProductCatalogKind = "resources" | "reports" | "knowledge-bases" | "teams" | "solvers" | "policies" | "skills";
export type ProductCatalogResult = CatalogAvailability & {
  kind: ProductCatalogKind;
  items: Array<Record<string, unknown>>;
  total: number;
};

export async function fetchProductCatalog(kind: ProductCatalogKind, query = ""): Promise<ProductCatalogResult> {
  const params = new URLSearchParams({ limit: "100" });
  if (query.trim()) params.set("query", query.trim());
  return requestJson<ProductCatalogResult>(`/api/v2/catalog/${kind}?${params.toString()}`);
}

export type ResourceTab = "artifacts" | "evidence" | "findings" | "knowledge";

export type ResourceSearchQuery = {
  taskId?: string;
  tab: ResourceTab;
  query?: string;
  status?: string;
};

export type ResourceRow = {
  id: string;
  taskId: string;
  kind: ResourceTab;
  title: string;
  status: string | null;
  type: string | null;
  sourceSolverId: string | null;
  sourceIntentId: string | null;
  artifactId: string | null;
  evidenceClaimIds: string[];
  locator: Record<string, unknown> | null;
  hash: string | null;
  target: string | null;
  createdAt: string | null;
  raw: Record<string, unknown>;
};

export type ResourceSearchResult = CatalogAvailability & {
  taskId: string | null;
  tab: ResourceTab;
  items: ResourceRow[];
  total: number;
};

type Page<T> = { items: T[]; total: number };
type TaskEvidenceResponse = {
  task_id: string;
  artifacts: Page<Record<string, unknown>>;
  evidence_claims: Page<Record<string, unknown>>;
  findings: Page<Record<string, unknown>>;
};

export async function fetchResourceSearch(query: ResourceSearchQuery): Promise<ResourceSearchResult> {
  const taskId = query.taskId?.trim();
  if (!taskId) {
    return unsupportedResource(query.tab, "当前 API 未提供跨任务资源聚合查询。请输入 Task ID 读取该任务的真实资源投影。");
  }
  if (query.tab === "knowledge") {
    return unsupportedResource(query.tab, "当前 API 未提供独立 Knowledge 列表；资源页不会从 Runtime 事件或 Artifact 推断 Knowledge。", taskId);
  }

  const response = await requestJson<TaskEvidenceResponse>(
    `/api/v2/tasks/${encodeURIComponent(taskId)}/evidence?offset=0&limit=100`,
  );
  const source = query.tab === "artifacts"
    ? response.artifacts.items
    : query.tab === "evidence"
      ? response.evidence_claims.items
      : response.findings.items;
  const normalized = source.map((item) => normalizeResourceRow(query.tab, taskId, item));
  const needle = query.query?.trim().toLocaleLowerCase();
  const items = normalized.filter((item) => {
    const matchesStatus = !query.status || item.status === query.status;
    const matchesText = !needle || [item.id, item.title, item.target, item.type]
      .some((value) => value?.toLocaleLowerCase().includes(needle));
    return matchesStatus && matchesText;
  });
  return { supported: true, reason: null, taskId, tab: query.tab, items, total: items.length };
}

function unsupportedResource(tab: ResourceTab, reason: string, taskId: string | null = null): ResourceSearchResult {
  return { supported: false, reason, taskId, tab, items: [], total: 0 };
}

function normalizeResourceRow(tab: ResourceTab, taskId: string, item: Record<string, unknown>): ResourceRow {
  if (tab === "artifacts") {
    const id = text(item.artifact_id) ?? "";
    return {
      id,
      taskId,
      kind: tab,
      title: text(item.target) ?? id,
      status: null,
      type: text(item.media_type) ?? text(item.kind),
      sourceSolverId: null,
      sourceIntentId: text(item.intent_id),
      artifactId: id,
      evidenceClaimIds: [],
      locator: null,
      hash: text(item.sha256),
      target: text(item.target),
      createdAt: text(item.created_at),
      raw: item,
    };
  }
  if (tab === "evidence") {
    const id = text(item.claim_id) ?? "";
    return {
      id,
      taskId,
      kind: tab,
      title: text(item.statement_preview) ?? id,
      status: text(item.status),
      type: "Evidence Claim",
      sourceSolverId: text(item.created_by_solver_id),
      sourceIntentId: null,
      artifactId: text(item.artifact_id),
      evidenceClaimIds: [],
      locator: record(item.locator),
      hash: null,
      target: null,
      createdAt: text(item.created_at),
      raw: item,
    };
  }
  const id = text(item.finding_id) ?? "";
  return {
    id,
    taskId,
    kind: tab,
    title: text(item.title) ?? id,
    status: text(item.status),
    type: text(item.severity),
    sourceSolverId: text(item.created_by_solver_id),
    sourceIntentId: null,
    artifactId: null,
    evidenceClaimIds: texts(item.evidence_claim_ids),
    locator: null,
    hash: null,
    target: text(item.target),
    createdAt: text(item.created_at),
    raw: item,
  };
}

export type ReportListQuery = { taskId?: string; query?: string };
export type ReportRecord = { taskId: string; markdown: string };
export type ReportListResult = CatalogAvailability & { items: ReportRecord[] };

export async function fetchReportList(query: ReportListQuery): Promise<ReportListResult> {
  const taskId = query.taskId?.trim();
  if (!taskId) {
    return {
      supported: false,
      reason: "当前 API 仅提供按 Task 读取报告正文，未提供报告目录、版本或状态聚合。请输入 Task ID 读取真实报告。",
      items: [],
    };
  }
  const response = await fetch(`${apiBase}/api/v2/tasks/${encodeURIComponent(taskId)}/report`);
  if (!response.ok) throw new ApiError(response.status, `报告读取失败（${response.status}）`);
  const markdown = await response.text();
  const needle = query.query?.trim().toLocaleLowerCase();
  const items = !needle || markdown.toLocaleLowerCase().includes(needle) ? [{ taskId, markdown }] : [];
  return { supported: true, reason: null, items };
}

export type KnowledgeBaseQuery = { scope?: string; query?: string; tab?: string };
export type KnowledgeBaseResult = CatalogAvailability & { items: never[] };

export async function fetchKnowledgeBases(_query: KnowledgeBaseQuery): Promise<KnowledgeBaseResult> {
  return {
    supported: false,
    reason: "当前 Retrieval Repository 尚未暴露全局 Knowledge Base HTTP 查询。页面不会直接读取 SQLite，也不会把任务 Transcript 或 Candidate Knowledge 伪装成知识库。",
    items: [],
  };
}

export type ConfigurationCatalogKind = "teams" | "solvers" | "policies";
export type ConfigurationCatalogQuery = { kind: ConfigurationCatalogKind; query?: string; tab?: string; status?: string };
export type ConfigurationCatalogResult = CatalogAvailability & { kind: ConfigurationCatalogKind; items: never[] };

export async function fetchConfigurationCatalog(query: ConfigurationCatalogQuery): Promise<ConfigurationCatalogResult> {
  const labels: Record<ConfigurationCatalogKind, string> = {
    teams: "Team Template",
    solvers: "Solver Definition",
    policies: "Policy/Budget Profile",
  };
  return {
    supported: false,
    reason: `当前 API 未提供 ${labels[query.kind]} Catalog。运行时 Team/Solver Snapshot 不会被当作可编辑的全局 Definition。`,
    kind: query.kind,
    items: [],
  };
}

export type SystemComponent = {
  id: string;
  label: string;
  status: "healthy" | "available" | "degraded" | "unavailable" | "unsupported";
  detail: string;
  version: string | null;
  latencyMs: number | null;
  lastSuccess: string | null;
  lastError: string | null;
};
export type SystemHealthResult = { components: SystemComponent[] };

type HealthResponse = { status: string; service: string };
type LlmHealth = {
  configured: boolean;
  model: string;
  verification_status?: string;
  verification?: { verified_at?: string | null; last_error?: { message?: string } | null };
};
type ToolHealth = { configured?: boolean; status?: string; records?: unknown[]; last_error?: string | null };
type CapabilitySnapshot = { capabilities?: unknown[] | Record<string, unknown>; tools?: unknown[] };

export async function fetchSystemHealth(): Promise<SystemHealthResult> {
  const started = performance.now();
  const [process, llm, tools, capabilities] = await Promise.all([
    requestJson<HealthResponse>("/api/health"),
    requestJson<LlmHealth>("/api/v2/settings/llm"),
    requestJson<ToolHealth>("/api/v2/tools/health"),
    requestJson<CapabilitySnapshot>("/api/v2/capabilities"),
  ]);
  const latencyMs = Math.max(0, Math.round(performance.now() - started));
  const capabilityCount = Array.isArray(capabilities.capabilities)
    ? capabilities.capabilities.length
    : Object.keys(capabilities.capabilities ?? {}).length;
  const toolCount = Array.isArray(capabilities.tools) ? capabilities.tools.length : 0;
  return {
    components: [
      component("runtime", "Execution Runtime", process.status === "ok" ? "healthy" : "degraded", process.service, latencyMs),
      component(
        "models",
        "Model Providers",
        llm.verification_status === "verified" ? "healthy" : llm.configured ? "degraded" : "unavailable",
        llm.configured ? `${llm.model || "已配置 Provider"} · ${llm.verification_status ?? "未验证"}` : "尚未配置模型 Provider",
        null,
        null,
        llm.verification?.verified_at ?? null,
        llm.verification?.last_error?.message ?? null,
      ),
      component(
        "mcp",
        "MCP Servers",
        tools.configured ? (tools.status === "error" ? "degraded" : "available") : "unavailable",
        tools.configured ? `${Array.isArray(tools.records) ? tools.records.length : 0} 个健康记录` : "未配置 MCP Catalog Runner",
        null,
        null,
        null,
        tools.last_error ?? null,
      ),
      component("capabilities", "Capability Catalog", "available", `${capabilityCount} 个 Capability · ${toolCount} 个 MCP Tool`, null),
      unsupportedComponent("scheduler", "Scheduler", "当前 API 未提供独立 Scheduler 诊断。"),
      unsupportedComponent("database", "Database", "当前 API 未提供脱敏 Database 健康查询。"),
      unsupportedComponent("artifacts", "Artifact Store", "当前 API 未提供全局 Artifact Store 健康查询。"),
      unsupportedComponent("events", "Event Stream", "当前 API 未提供全局 Event Stream 健康查询。"),
      unsupportedComponent("retrieval", "Retrieval Indexes", "当前 API 未提供全局 Retrieval Index 诊断。"),
    ],
  };
}

function component(
  id: string,
  label: string,
  status: SystemComponent["status"],
  detail: string,
  latencyMs: number | null,
  version: string | null = null,
  lastSuccess: string | null = null,
  lastError: string | null = null,
): SystemComponent {
  return { id, label, status, detail, latencyMs, version, lastSuccess, lastError };
}

function unsupportedComponent(id: string, label: string, detail: string): SystemComponent {
  return component(id, label, "unsupported", detail, null);
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.length ? value : null;
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
