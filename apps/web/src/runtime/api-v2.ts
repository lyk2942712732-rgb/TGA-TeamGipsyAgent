import { apiBase, ApiError, requestJson } from "../api/client";
import { normalizeRuntimeEvent, normalizeRuntimeSnapshot } from "../features/runtime/models/normalize";
import type { RuntimeStore } from "../features/runtime/models/types";
import type { CapabilityCatalog, MCPHealth, MCPManagedServer, MCPServerConfig, MCPServerTools } from "./event-types";

export type ArtifactPreviewResponse = {
  artifact: {
    id: string;
    kind?: string;
    tool?: string;
    target?: string;
    created_at?: string;
    sha256?: string;
    [key: string]: unknown;
  };
  preview: string;
  truncated?: boolean;
  redactions?: number;
  byte_limit?: number;
  download_url?: string | null;
};

const url = (path: string) => `${apiBase}/api/v2${path}`;
export class RuntimeApiError extends ApiError {}
async function get<T>(path: string): Promise<T> {
  return requestJson<T>(`/api/v2${path}`);
}

export const runtimeApi = {
  taskRuntime: async (taskId: string): Promise<RuntimeStore> => normalizeRuntimeSnapshot(await get<unknown>(`/tasks/${encodeURIComponent(taskId)}/session`)),
  runtimeEvents: async (taskId: string, afterSeq: number) => {
    const value = await get<{ events: unknown[]; latest_seq: number; has_more?: boolean }>(`/tasks/${encodeURIComponent(taskId)}/events?after_seq=${afterSeq}`);
    return { events: value.events.map(normalizeRuntimeEvent), latestSeq: value.latest_seq, hasMore: Boolean(value.has_more) };
  },
  capabilities: () => get<CapabilityCatalog>("/capabilities"),
  toolHealth: () => get<MCPHealth>("/tools/health"),
  mcpServers: () => requestJson<{ servers: MCPManagedServer[] }>("/api/v2/mcp/servers"),
  createMCPServer: (id: string, config: Partial<MCPServerConfig>) => requestJson<{ action: string; server: MCPManagedServer }>("/api/v2/mcp/servers", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, config }),
  }),
  updateMCPServer: (id: string, patch: Record<string, unknown>) => requestJson<{ server: MCPManagedServer }>(`/api/v2/mcp/servers/${encodeURIComponent(id)}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  }),
  deleteMCPServer: (id: string) => requestJson<{ deleted: boolean; server_id: string; image_deleted: false }>(`/api/v2/mcp/servers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  refreshMCPServer: (id: string) => requestJson<MCPManagedServer>(`/api/v2/mcp/servers/${encodeURIComponent(id)}/refresh`, { method: "POST" }),
  testMCPServer: (id: string) => requestJson<MCPServerTools>(`/api/v2/mcp/servers/${encodeURIComponent(id)}/tools`),
  testMCPMethod: (id: string, method: string, argumentsValue: Record<string, unknown>, confirmActive: boolean) => requestJson<{ ok: boolean; trace_id: string; request_id: string; timings: Record<string, number>; content_preview: string; error?: { code?: string; message?: string } | null }>(`/api/v2/mcp/servers/${encodeURIComponent(id)}/tools/${encodeURIComponent(method)}/test`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ arguments: argumentsValue, confirm_active: confirmActive }),
  }),
  artifact: (taskId: string, artifactId: string) => get<ArtifactPreviewResponse>(`/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}`),
  artifactUrl: (taskId: string, artifactId: string) => url(`/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}`),
  artifactDownloadUrl: (taskId: string, artifactId: string) => url(`/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}?download=true`),
  reportUrl: (taskId: string) => `${apiBase}/api/v2/tasks/${encodeURIComponent(taskId)}/report`,
  control: async (taskId: string, action: "pause" | "resume" | "cancel" | "approve_action" | "reject_action", actionId?: string) => {
    return requestJson<{ accepted?: boolean; status?: string; reason?: string }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/control`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, ...(actionId ? { action_id: actionId } : {}) }) });
  },
  intervention: async (taskId: string, payload: { kind: "hint" | "instruction" | "constraint" | "priority_change" | "answer"; content: string; scope: "task" | "solver" | "intent"; target_id?: string }) => requestJson<{ accepted?: boolean; status?: string; intervention?: { id?: string } }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/interventions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  approvalDecision: async (taskId: string, actionId: string, decision: "approve" | "reject") => requestJson<{ accepted?: boolean; status?: string }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/approvals/${encodeURIComponent(actionId)}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) }),
  solverControl: async (taskId: string, solverId: string, action: "pause" | "resume" | "cancel") => requestJson<{ accepted?: boolean; status?: string }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/solvers/${encodeURIComponent(solverId)}/control`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) }),
  retryIntent: async (taskId: string, intentId: string) => requestJson<{ accepted?: boolean; status?: string }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/intents/${encodeURIComponent(intentId)}/retry`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }),
  streamUrl: (taskId: string, afterSeq: number) => url(`/tasks/${encodeURIComponent(taskId)}/events/stream?after_seq=${afterSeq}`),
};
