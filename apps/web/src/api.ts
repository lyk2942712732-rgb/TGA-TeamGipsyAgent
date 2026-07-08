export type TGATask = {
  id: string;
  name: string;
  mode: "ctf" | "web_audit" | "code_audit" | "binary_ctf";
  target: string;
  scope: string[];
  intensity: "passive" | "normal" | "active";
  allow_active_scan: boolean;
  goal: string;
  flag_format?: string | null;
};

export type CreateTaskResponse = {
  task_id: string;
  status: string;
  report_path?: string | null;
  run_root?: string | null;
};

export type SnapshotEvent = {
  id?: number;
  intent_id?: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type TaskSnapshot = {
  task?: TGATask | null;
  intents?: Array<Record<string, unknown>>;
  artifacts?: Array<Record<string, unknown>>;
  findings?: Array<Record<string, unknown>>;
  flags?: Array<Record<string, unknown>>;
  events?: SnapshotEvent[];
};

export type TaskSnapshotResponse = {
  task_id: string;
  snapshot: TaskSnapshot;
};

const configuredApiBase = import.meta.env.VITE_TGA_API_BASE as string | undefined;
const API_BASE = (configuredApiBase ?? "http://127.0.0.1:8000").replace(/\/$/, "");

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export async function createTask(task: TGATask): Promise<CreateTaskResponse> {
  const response = await fetch(apiUrl("/api/tasks"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task }),
  });
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchTaskSnapshot(taskId: string): Promise<TaskSnapshotResponse> {
  const response = await fetch(apiUrl(`/api/tasks/${encodeURIComponent(taskId)}`));
  if (!response.ok) {
    throw new Error(`Snapshot request failed: ${response.status}`);
  }
  return response.json();
}

export function reportUrl(taskId: string): string {
  return apiUrl(`/api/tasks/${encodeURIComponent(taskId)}/report`);
}
