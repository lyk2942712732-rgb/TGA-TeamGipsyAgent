import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, stateLabel } from "./App";

const task = {
  id: "11111111-1111-4111-8111-111111111111",
  title: "寻找最终 flag",
  scene_id: "penetration_test",
  state: "running",
  blackboard_seq: 2,
  dialogue_seq: 3,
  final_snapshot_seq: null,
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:01:00Z",
};

const agents = [
  {
    task_id: task.id, agent_id: "supervisor", display_name: "Supervisor", role: "supervisor", runtime_location: "host",
    sdk: "openai_agents", desired_state: "idle", actual_state: "idle", provider_id: "openai",
    model_id: "openai-worker-model", container_id: null, session_id: null, last_error: null, updated_at: task.updated_at,
  },
  {
    task_id: task.id,
    agent_id: "worker-openai",
    display_name: "OpenAI Worker",
    role: "worker",
    runtime_location: "container",
    sdk: "openai_agents",
    desired_state: "running",
    actual_state: "running",
    provider_id: "openai",
    model_id: "openai-worker-model",
    container_id: "abcdef0123456789",
    session_id: "session-openai",
    last_error: null,
    updated_at: task.updated_at,
  },
  {
    task_id: task.id,
    agent_id: "worker-claude",
    display_name: "Claude Worker",
    role: "worker",
    runtime_location: "container",
    sdk: "claude_agent",
    desired_state: "running",
    actual_state: "running",
    provider_id: "anthropic",
    model_id: "claude-worker-model",
    container_id: "fedcba9876543210",
    session_id: "session-claude",
    last_error: null,
    updated_at: task.updated_at,
  },
  {
    task_id: task.id, agent_id: "reporter", display_name: "Reporter", role: "reporter", runtime_location: "host",
    sdk: "openai_agents", desired_state: "created", actual_state: "created", provider_id: "openai",
    model_id: "openai-worker-model", container_id: null, session_id: null, last_error: null, updated_at: task.updated_at,
  },
];

const configBundle = {
  models: { schema_version: 1, providers: [
    { id: "openai", name: "OpenAI", protocol: "openai_responses", base_url: "https://api.openai.com/v1", api_keys: [{ id: "primary", label: "Primary", api_key: "sk-local" }], selected_api_key_id: "primary", models: [{ id: "openai-worker-model", name: "gpt-test", max_output_tokens: 8192, timeout_seconds: 180 }] },
    { id: "anthropic", name: "Anthropic", protocol: "anthropic", base_url: "https://api.anthropic.com", api_keys: [{ id: "primary", label: "Primary", api_key: "claude-local" }], selected_api_key_id: "primary", models: [{ id: "claude-worker-model", name: "claude-test", max_output_tokens: 8192, timeout_seconds: 180 }] },
  ] },
  agents: { schema_version: 1, agents: Object.fromEntries(agents.map((agent) => [agent.agent_id, {
    display_name: agent.display_name, role: agent.role, runtime: agent.sdk, provider_id: agent.provider_id,
    model_id: agent.model_id, max_turns_per_cycle: 3, system_prompt: `${agent.display_name} system prompt`,
  }])) },
  scenes: { schema_version: 1, scenes: [{ id: "penetration_test", name: "渗透测试", description: "Web 与网络服务题", system_prompt: "找到并验证 flag" }] },
  runtime: { schema_version: 1, postgres_dsn: "postgresql://local", listen_port: 8083 },
};

class EventSourceStub {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  constructor(_url: string) { setTimeout(() => this.onopen?.(), 0); }
  close() { return undefined; }
}

function response(value: unknown) {
  return Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } }));
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={client}><App /></QueryClientProvider></MemoryRouter>);
}

describe("TGA3 frontend", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", EventSourceStub);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v3/tasks")) return response([task]);
      if (url.endsWith(`/api/v3/tasks/${task.id}`)) return response({ task, agents });
      if (url.endsWith(`/api/v3/tasks/${task.id}/blackboard`)) return response({
        published: null,
        latest_seq: 2,
        entries: [{
          id: "finding-1",
          task_id: task.id,
          seq: 2,
          actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker", sdk: "openai_agents", model: "test" },
          kind: "finding",
          topic: "flag",
          body: { claim: "已验证 flag 位于输出第一行" },
          idempotency_key: "finding",
          created_at: task.updated_at,
        }],
      });
      if (url.endsWith(`/api/v3/tasks/${task.id}/dialogue`)) return response([]);
      if (url.endsWith("/api/v3/models")) return response({ providers: [], bindings: {
        "worker-openai": { display_name: "OpenAI Worker", role: "worker", runtime: "openai_agents", provider_id: "openai", model_id: "openai-worker-model", max_turns_per_cycle: 3 },
      } });
      if (url.endsWith("/api/v3/scenes")) return response([{ id: "penetration_test", name: "渗透测试", description: "Web 与网络服务题" }]);
      if (url.endsWith("/api/v3/config")) return response(configBundle);
      if (url.endsWith("/api/v3/skills")) return response([]);
      return response({});
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("uses the TGA3 task vocabulary", async () => {
    renderAt("/tasks");
    expect(await screen.findByText("寻找最终 flag")).toBeInTheDocument();
    expect(screen.getByText("黑板序号")).toBeInTheDocument();
  });

  it("renders dual workers and the upgraded dialogue inspector", async () => {
    renderAt(`/tasks/${task.id}`);
    expect((await screen.findAllByText("OpenAI Worker")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Claude Worker").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "对话" })).toBeInTheDocument();
    expect(screen.getByText("过程摘要与动作")).toBeInTheDocument();
    expect(await screen.findByText("已验证 flag 位于输出第一行")).toBeInTheDocument();
    expect(screen.getByText("状态来自后端 agent_runs")).toBeInTheDocument();
  });

  it("opens the config-backed scene task modal", async () => {
    renderAt("/tasks/new");
    expect(await screen.findByRole("dialog", { name: "创建任务" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /渗透测试/ })).toBeInTheDocument();
    expect(screen.getByText("共享黑板")).toBeInTheDocument();
    expect(await screen.findByText("openai/openai-worker-model")).toBeInTheDocument();
  });

  it("reads the editable config source in the frontend", async () => {
    renderAt("/config");
    expect(await screen.findByText("统一配置中心")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("OpenAI")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加供应商" })).toBeInTheDocument();
    expect(screen.getAllByLabelText("API Key")[0]).toHaveValue("sk-local");
  });

  it("maps backend states without legacy status semantics", () => {
    expect(stateLabel("waiting_user")).toBe("等待用户");
    expect(stateLabel("reporting")).toBe("生成报告");
  });
});
