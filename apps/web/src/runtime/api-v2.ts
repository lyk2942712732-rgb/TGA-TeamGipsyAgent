import { apiBase, ApiError, requestJson } from "../api/client";
import { AgentEventSchema, RuntimeSnapshotSchema } from "../api/schemas";
import type { CapabilityCatalog, MCPDeleteResult, MCPEnabledResult, MCPHealth, MCPImportResult, MCPManagedServer, MCPServerConfig, MCPServerTools, RuntimeEvent, RuntimeSnapshot } from "./event-types";

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

function uploadMCP(file: File, onProgress?: (percent: number) => void, signal?: AbortSignal): Promise<MCPImportResult> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `${apiBase}/api/v2/mcp/images/import`);
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.setRequestHeader("X-TGA-Filename", encodeURIComponent(file.name));
    request.upload.onprogress = (event) => { if (event.lengthComputable) onProgress?.(Math.round(event.loaded / event.total * 100)); };
    request.onload = () => {
      let payload: unknown;
      try { payload = JSON.parse(request.responseText); } catch { payload = null; }
      if (request.status >= 200 && request.status < 300) resolve(payload as MCPImportResult);
      else reject(new Error((payload as { detail?: string } | null)?.detail ?? `MCP import failed (${request.status})`));
    };
    request.onerror = () => reject(new Error("MCP image upload failed"));
    request.onabort = () => reject(new DOMException("MCP image import cancelled", "AbortError"));
    if (signal) {
      if (signal.aborted) { request.abort(); return; }
      signal.addEventListener("abort", () => request.abort(), { once: true });
    }
    request.send(file);
  });
}

export const runtimeApi = {
  session: async (taskId: string) => RuntimeSnapshotSchema.parse(await get<unknown>(`/tasks/${encodeURIComponent(taskId)}/session`)) as RuntimeSnapshot,
  events: async (taskId: string, afterSeq: number) => { const value = await get<{ events: unknown[]; latest_seq: number }>(`/tasks/${encodeURIComponent(taskId)}/events?after_seq=${afterSeq}`); return { events: value.events.map((event) => AgentEventSchema.parse(event) as RuntimeEvent), latest_seq: value.latest_seq }; },
  capabilities: () => get<CapabilityCatalog>("/capabilities"),
  toolHealth: () => get<MCPHealth>("/tools/health"),
  refreshMCP: () => requestJson<MCPHealth>("/api/v2/tools/mcp/refresh", { method: "POST" }),
  importMCP: (file: File, onProgress?: (percent: number) => void, signal?: AbortSignal) => uploadMCP(file, onProgress, signal),
  deleteMCP: (serverId: string) => requestJson<MCPDeleteResult>(`/api/v2/tools/mcp/${encodeURIComponent(serverId)}`, { method: "DELETE" }),
  setMCPEnabled: (serverId: string, enabled: boolean) => requestJson<MCPEnabledResult>(`/api/v2/tools/mcp/${encodeURIComponent(serverId)}/enabled`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  }),
  mcpServers: () => requestJson<{ servers: MCPManagedServer[] }>("/api/v2/mcp/servers"),
  createMCPServer: (id: string, config: Partial<MCPServerConfig>) => requestJson<{ action: string; server: MCPManagedServer }>("/api/v2/mcp/servers", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, config }),
  }),
  updateMCPServer: (id: string, patch: Record<string, unknown>) => requestJson<{ server: MCPManagedServer }>(`/api/v2/mcp/servers/${encodeURIComponent(id)}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  }),
  testMCPServer: (id: string) => requestJson<MCPServerTools>(`/api/v2/mcp/servers/${encodeURIComponent(id)}/tools`),
  testMCPMethod: (id: string, method: string, argumentsValue: Record<string, unknown>, confirmActive: boolean) => requestJson<{ ok: boolean; trace_id: string; request_id: string; timings: Record<string, number>; content_preview: string; error?: { code?: string; message?: string } | null }>(`/api/v2/mcp/servers/${encodeURIComponent(id)}/tools/${encodeURIComponent(method)}/test`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ arguments: argumentsValue, confirm_active: confirmActive }),
  }),
  inspectMCPImage: (image: string) => requestJson<{ image: string; local: boolean; details: Record<string, unknown> }>(`/api/v2/mcp/images/${encodeURIComponent(image)}/inspect`, { method: "POST" }),
  artifact: (taskId: string, artifactId: string) => get<ArtifactPreviewResponse>(`/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}`),
  artifactUrl: (taskId: string, artifactId: string) => url(`/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}`),
  reportUrl: (taskId: string) => `${apiBase}/api/v2/tasks/${encodeURIComponent(taskId)}/report`,
  control: async (taskId: string, action: "pause" | "resume" | "cancel") => {
    return requestJson<{ accepted?: boolean; status?: string }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/control`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
  },
  hint: async (taskId: string, content: string) => {
    return requestJson<{ accepted?: boolean }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/hints`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }) });
  },
  streamUrl: (taskId: string, afterSeq: number) => url(`/tasks/${encodeURIComponent(taskId)}/events/stream?after_seq=${afterSeq}`),
};
