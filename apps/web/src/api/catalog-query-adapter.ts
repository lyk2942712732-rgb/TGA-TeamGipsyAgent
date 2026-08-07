import { requestJson } from "./client";

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

export type SolverBudget = {
  max_turns?: number;
  max_input_tokens?: number;
  max_output_tokens?: number;
  max_tool_calls?: number;
  max_artifacts?: number;
  deadline?: string | null;
};

export type SolverDefinitionRecord = {
  id: string;
  version: string;
  role: "supervisor" | "worker" | "reviewer" | "reporter";
  specialties: string[];
  supported_modes: string[];
  supported_subtypes: string[];
  system_prompt_template: string;
  default_skill_tags: string[];
  required_skill_names: string[];
  host_capability_profile_id: string;
  host_capability_overrides: { add: string[]; remove: string[] };
  host_capabilities: HostCapabilityAssignment[];
  kali: SolverKaliDetail | null;
  accepted_intent_kinds: string[];
  output_contract: { name: string; required_fields: string[] };
  default_budget: SolverBudget;
  completion_authority: string;
  content_sha256: string;
};

export async function fetchSolverDefinitions(query = ""): Promise<{ items: SolverDefinitionRecord[]; total: number }> {
  const params = new URLSearchParams();
  if (query.trim()) params.set("query", query.trim());
  const suffix = params.size ? `?${params.toString()}` : "";
  return requestJson<{ items: SolverDefinitionRecord[]; total: number }>(`/api/v2/solvers${suffix}`);
}

export type HostCapabilityAssignment = {
  id: string;
  display_name: string;
  category: string;
  risk: string;
  source: string;
};

export type KaliTool = { name: string; executable: string; version: string | null; category: string | null };
export type KaliLimits = { cpu_cores: number; memory_mb: number; timeout_seconds: number; max_processes: number };
export type SolverKaliDetail = {
  profile_id: string;
  capabilities: Array<"kali.exec" | "kali.session">;
  image_name: string;
  image_tag: string;
  image_digest: string | null;
  allowed_executables: string[];
  session_executables: string[];
  network_mode: string;
  limits: KaliLimits;
  tools: KaliTool[];
};

export type KaliHealthStatus =
  | "host_only"
  | "unknown"
  | "runtime_disabled"
  | "unresolved_digest"
  | "image_unreachable"
  | "image_not_found"
  | "image_unverified"
  | "toolset_mismatch"
  | "tools_missing"
  | "runtime_unavailable"
  | "healthy";

export type SolverKaliHealthSummary = {
  solver_id: string;
  requires_kali: boolean;
  profile_id: string | null;
  status: KaliHealthStatus;
};

export type SolverKaliHealth = SolverKaliHealthSummary & {
  image: string | null;
  image_status: string;
  runtime_status: string;
  checked_at: string | null;
  reasons: Array<{ code: string; message: string }>;
  missing_executables: string[];
  image_store: {
    status: "not_applicable" | "unknown" | "unreadable" | "readable";
    error: string | null;
  };
  toolset: {
    expected_digest: string | null;
    actual_digest: string | null;
    status: string;
  };
};

export type HostCapabilityRecord = {
  id: string;
  display_name: string;
  category: string;
  description: string;
  allowed_roles: string[];
  risk: string;
  input_schema: { properties?: Record<string, unknown>; required?: string[] };
  output_schema: Record<string, unknown>;
  handler_key: string;
  handler_status: string;
  assigned_solver_count: number;
  assigned_solver_ids: string[];
};

export type HostCapabilityProfileRecord = {
  id: string;
  capability_ids: string[];
};

export type KaliCapabilityRecord = {
  id: "kali.exec" | "kali.session";
  display_name: string;
  description: string;
  risk: string;
  input_schema: { properties?: Record<string, unknown>; required?: string[] };
  assigned_solver_count: number;
  assigned_solver_ids: string[];
  profile_ids: string[];
};

export type KaliProfileRecord = {
  id: string;
  display_name: string;
  image_name: string;
  image_tag: string;
  image_digest: string | null;
  image: string;
  image_role: "dedicated" | "universal";
  shared_image_profile_count: number;
  tools: KaliTool[];
  supported_capabilities: Array<"kali.exec" | "kali.session">;
  allowed_executables: string[];
  session_executables: string[];
  network_mode: string;
  input_mount: string;
  scratch_mount: string;
  shared_artifact_mount: string;
  limits: KaliLimits;
  enabled: boolean;
  assigned_solver_count: number;
  assigned_solver_ids: string[];
  config_sha256: string;
};

export const fetchHostCapabilities = () => requestJson<{ items: HostCapabilityRecord[]; total: number }>("/api/v2/capabilities/host");
export const fetchHostCapabilityProfiles = () => requestJson<{ items: HostCapabilityProfileRecord[]; total: number }>("/api/v2/capabilities/host-profiles");
export const fetchKaliCapabilities = () => requestJson<{ items: KaliCapabilityRecord[]; total: number }>("/api/v2/capabilities/kali");
export const fetchKaliProfiles = () => requestJson<{ items: KaliProfileRecord[]; total: number }>("/api/v2/kali/profiles");
export const fetchSolverKaliHealth = (id: string) => requestJson<SolverKaliHealth>(`/api/v2/solvers/${encodeURIComponent(id)}/kali-health`);
export const fetchSolverKaliHealthSummary = () => requestJson<{ items: SolverKaliHealthSummary[]; total: number }>("/api/v2/solvers/kali-health");
export const checkSolverKaliHealth = (id: string) => requestJson<SolverKaliHealth>(`/api/v2/solvers/${encodeURIComponent(id)}/kali-health/check`, { method: "POST" });
export const fetchSolverDefinition = (id: string) => requestJson<SolverDefinitionRecord>(`/api/v2/solvers/${encodeURIComponent(id)}`);
export const fetchSolverManifest = (id: string, mode?: string) => requestJson<Record<string, unknown>>(`/api/v2/solvers/${encodeURIComponent(id)}/manifest-preview${mode ? `?mode=${encodeURIComponent(mode)}` : ""}`);
export const updateSolverCapabilities = (
  id: string,
  payload: Pick<SolverDefinitionRecord, "host_capability_profile_id" | "host_capability_overrides"> & { expected_content_sha256: string; kali: { profile_id: string; capabilities: Array<"kali.exec" | "kali.session"> } | null },
) => requestJson<SolverDefinitionRecord>(`/api/v2/solvers/${encodeURIComponent(id)}/capabilities`, {
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

/** Team templates, served verbatim by `/api/v2/catalog/teams`. */
export type TeamTemplateRecord = {
  mode: string;
  supervisor_definition_id: string;
  required_solver_definition_ids: string[];
  available_solver_definition_ids: string[];
  reviewer_definition_id: string;
  reporter_definition_id: string;
  spawn_rules: Array<{ trigger: string; definition_id: string; max_instances: number }>;
  max_active_workers: number;
  max_total_solvers: number;
  completion_policy: Record<string, boolean>;
  content_sha256: string;
};

export async function fetchTeamTemplates(query = ""): Promise<{ items: TeamTemplateRecord[]; total: number }> {
  const result = await fetchProductCatalog("teams", query);
  return { items: result.items as unknown as TeamTemplateRecord[], total: result.total };
}

/**
 * ExecutionPolicy presets, served verbatim by `/api/v2/catalog/policies`.  One
 * record per task mode, matching `tga.modes.default_execution_policy`.
 */
export type ExecutionPolicyContract = {
  preset: string;
  network: {
    access: string;
    interaction: string;
    seed_origins: string[];
    custom_origins: string[];
    custom_domains: string[];
    custom_cidrs: string[];
    custom_ports: number[];
    deny_private_networks: boolean;
    deny_loopback: boolean;
    deny_link_local: boolean;
    deny_cloud_metadata: boolean;
    rate_limit_per_minute: number;
    concurrency: number;
    request_timeout_seconds: number;
  };
  local_compute: {
    mode: string;
    timeout_seconds: number;
    concurrency: number;
    network_inheritance: string;
  };
  high_impact: { mode: string; allowed_actions: string[] };
};

export type ExecutionPolicyRecord = {
  id: string;
  type: string;
  mode: string;
  mode_label: string;
  preset: string;
  status: string;
  source: string;
  editable: boolean;
  execution_policy: ExecutionPolicyContract;
};

export async function fetchExecutionPolicies(query = ""): Promise<{ items: ExecutionPolicyRecord[]; total: number }> {
  const result = await fetchProductCatalog("policies", query);
  return { items: result.items as unknown as ExecutionPolicyRecord[], total: result.total };
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
type CapabilitySnapshot = { host: unknown[]; kali: unknown[] };

export async function fetchSystemHealth(): Promise<SystemHealthResult> {
  const started = performance.now();
  const [process, llm, tools, capabilities] = await Promise.all([
    requestJson<HealthResponse>("/api/health"),
    requestJson<LlmHealth>("/api/v2/settings/llm"),
    requestJson<ToolHealth>("/api/v2/tools/health"),
    requestJson<CapabilitySnapshot>("/api/v2/capabilities"),
  ]);
  const latencyMs = Math.max(0, Math.round(performance.now() - started));
  const capabilityCount = capabilities.host.length + capabilities.kali.length;
  const toolCount = Array.isArray(tools.records) ? tools.records.length : 0;
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
        tools.configured ? `${Array.isArray(tools.records) ? tools.records.length : 0} 个已配置` : "未配置 MCP Catalog Runner",
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
