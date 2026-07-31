import type { TaskMode } from "../modes";
export type { TaskMode } from "../modes";

export type Capability = { name: string; availability: string; risk: string; modes: TaskMode[]; tools?: Array<{ tool_id: string; availability: string; detail?: string }> };
export type MCPTool = { tool_id: string; provider_name?: string; risk: string; modes?: TaskMode[]; methods: Array<{ name: string; description?: string; input_schema?: Record<string, unknown> }> };
export type MCPCatalog = { availability: string; reason?: string; tools: MCPTool[] };
export type MCPHealthRecord = { tool?: string; server?: string; status?: string; detail?: string; configured?: boolean; enabled?: boolean; reachable?: boolean; discovered?: boolean; visible_for_task?: number | null; runnable?: boolean | null; last_call_at?: string | null; last_call_method?: string | null; last_call_duration_ms?: number | null; last_call_error?: { code?: string; message?: string } | null; tools?: number; transport?: "stdio" | "streamable_http"; protocol_version?: string; server_info?: Record<string, unknown>; discovered_at?: string | null; image?: string | null; endpoint?: string | null; workspace_access?: { mode?: "automatic" | "remote" | "host_process"; mounted_on_task_call?: boolean; container_path?: string; read_only?: boolean; artifacts_path?: string; artifacts_writable?: boolean }; error?: { code?: string; message?: string; phase?: string; retryable?: boolean } | null };
export type MCPHealth = { configured: boolean; checked_at?: string; records: MCPHealthRecord[] };
export type MCPImportResult = { server_id: string; image: string; images?: string[]; requires_selection?: boolean; source_type: "docker-image" | "docker-build" | string; config_path: string; config_action: "created" | "updated" | string; build_log?: string; catalog?: MCPHealth };
export type MCPServerConfig = { enabled: boolean; transport: "stdio" | "streamable_http"; enabledTools?: string[]; stdio?: { source: "docker_image" | "local_process"; image?: string; command?: string; args?: string[]; docker?: { memory?: string | null; cpus?: number | null; pidsLimit?: number | null; network?: string; readOnly?: boolean; capDropAll?: boolean; noNewPrivileges?: boolean } } | null; http?: { url: string; verifyTls: boolean; headers?: Record<string, string>; secretRefs?: Record<string, string>; proxyUrl?: string | null; allowSameOriginRedirects?: boolean } | null };
export type MCPManagedServer = { id: string; config: MCPServerConfig; status?: MCPHealthRecord | null };
export type MCPServerTools = { server_id: string; status: string; protocol_version?: string; server_info?: Record<string, unknown>; error?: { code?: string; message?: string } | null; tools: Array<{ name: string; description?: string; input_schema?: Record<string, unknown>; enabled: boolean }> };
export type CapabilityCatalog = { capabilities: Capability[]; tools: MCPCatalog };

export type {
  RuntimeApproval,
  RuntimeBudgetUsage,
  RuntimeEvidenceClaim,
  RuntimeGlobalPlan,
  RuntimeIntent,
  RuntimeKnowledgeItem,
  RuntimeRetrievalSummary,
  RuntimeSolver as TaskRuntimeSolver,
  RuntimeWorkerResult,
} from "../features/runtime/models/types";
