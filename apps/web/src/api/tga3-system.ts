import { requestJson } from "./client";

export type SystemComponent = {
  id: string;
  label: string;
  status: "healthy" | "available" | "degraded" | "unavailable";
  detail: string;
  latencyMs: number | null;
};

export type SystemHealth = { components: SystemComponent[] };

export async function fetchSystemHealth(): Promise<SystemHealth> {
  const started = performance.now();
  const [process, models, skills] = await Promise.all([
    requestJson<{ status: string; service: string }>("/api/v3/health"),
    requestJson<{ providers: unknown[]; bindings: Record<string, unknown> }>("/api/v3/models"),
    requestJson<Array<{ name: string }>>("/api/v3/skills"),
  ]);
  const latency = Math.max(0, Math.round(performance.now() - started));
  return {
    components: [
      { id: "runtime", label: "TGA3 Control Plane", status: process.status === "ok" ? "healthy" : "degraded", detail: process.service, latencyMs: latency },
      { id: "models", label: "Model Providers", status: models.providers.length ? "available" : "unavailable", detail: `${models.providers.length} 个供应商 · ${Object.keys(models.bindings).length} 个 Agent 绑定`, latencyMs: null },
      { id: "skills", label: "Skills", status: "available", detail: `${skills.length} 个按需读取 Skill`, latencyMs: null },
      { id: "workers", label: "Worker Containers", status: "available", detail: "容器由任务生命周期按需启动", latencyMs: null },
    ],
  };
}
