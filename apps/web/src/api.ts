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

export async function createTask(task: TGATask): Promise<{ task_id: string; status: string }> {
  const response = await fetch("http://127.0.0.1:8000/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task }),
  });
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json();
}

