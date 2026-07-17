import { apiBase, ApiError, requestJson } from "../api/client";
import { AgentEventSchema, RuntimeSnapshotSchema } from "../api/schemas";
import type { CapabilityCatalog, MCPHealth, RuntimeEvent, RuntimeSnapshot } from "./event-types";

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
  session: async (taskId: string) => RuntimeSnapshotSchema.parse(await get<unknown>(`/tasks/${encodeURIComponent(taskId)}/session`)) as RuntimeSnapshot,
  events: async (taskId: string, afterSeq: number) => { const value = await get<{ events: unknown[]; latest_seq: number }>(`/tasks/${encodeURIComponent(taskId)}/events?after_seq=${afterSeq}`); return { events: value.events.map((event) => AgentEventSchema.parse(event) as RuntimeEvent), latest_seq: value.latest_seq }; },
  capabilities: () => get<CapabilityCatalog>("/capabilities"),
  toolHealth: () => get<MCPHealth>("/tools/health"),
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
