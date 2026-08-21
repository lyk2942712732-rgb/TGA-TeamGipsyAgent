import { apiBase, ApiError, requestJson } from "../api/client";
import { normalizeRuntimeEvent, normalizeRuntimeSnapshot } from "../features/runtime/models/normalize";
import type { RuntimeStore } from "../features/runtime/models/types";
import type { StagedAsset } from "../api/tasks";
import { takeStagedFile } from "../api/tasks";
import { loadTGA3Runtime } from "./tga3-runtime";
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
  taskRuntime: async (taskId: string): Promise<RuntimeStore> => loadTGA3Runtime(taskId),
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
  reportUrl: (taskId: string) => `${apiBase}/api/v3/tasks/${encodeURIComponent(taskId)}/writeup/download`,
  control: async (taskId: string, action: "cancel"): Promise<{ accepted?: boolean; status?: string; reason?: string }> => {
    void action;
    await requestJson<void>(`/api/v3/tasks/${encodeURIComponent(taskId)}/stop`, { method: "POST" });
    return { accepted: true, status: "cancelled" };
  },
  solverMessage: async (taskId: string, solverId: string, content: string, attachments: StagedAsset[]) => {
    const attachmentIds: string[] = [];
    for (const asset of attachments) { const file = takeStagedFile(asset.id); if (!file) continue; const form = new FormData(); form.set("file", file, file.name); const uploaded = await requestJson<{ file: { id: string } }>(`/api/v3/tasks/${encodeURIComponent(taskId)}/files`, { method: "POST", body: form }); attachmentIds.push(uploaded.file.id); }
    const message = await requestJson<{ id: string }>(`/api/v3/tasks/${encodeURIComponent(taskId)}/prompts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: content || "请查看附件", addressed_to: [solverId], attachment_ids: attachmentIds, idempotency_key: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}` }) });
    return { accepted: true, status: "accepted", message_id: message.id };
  },
  solverControl: async (taskId: string, solverId: string, action: "pause" | "resume") => requestJson<{ actual_state: string }>(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(solverId)}/${action}`, { method: "POST" }).then((value) => ({ accepted: true, status: value.actual_state })),
  solverModel: async (taskId: string, solverId: string, providerId: string, modelId: string) => requestJson<Record<string, unknown>>(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(solverId)}/model`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider_id: providerId, model_id: modelId }) }).then((model) => ({ model })),
  intervention: async (taskId: string, payload: { kind: "hint" | "instruction" | "constraint" | "priority_change" | "answer"; content: string; scope: "task" | "solver" | "intent"; target_id?: string }) => requestJson<{ accepted?: boolean; status?: string; intervention?: { id?: string } }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/interventions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  userInput: async (taskId: string, content: string) => requestJson<Record<string, unknown>>(`/api/v3/tasks/${encodeURIComponent(taskId)}/prompts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: content, addressed_to: ["supervisor"], attachment_ids: [], idempotency_key: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}` }) }).then(() => ({ status: "accepted" })),
  approvalDecision: async (taskId: string, actionId: string, decision: "approve" | "reject") => requestJson<{ accepted?: boolean; status?: string }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/approvals/${encodeURIComponent(actionId)}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) }),
  streamUrl: (taskId: string, afterSeq: number) => url(`/tasks/${encodeURIComponent(taskId)}/events/stream?after_seq=${afterSeq}`),
};
