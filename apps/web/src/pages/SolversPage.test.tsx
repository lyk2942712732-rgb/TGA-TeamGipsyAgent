import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ agents: vi.fn(), models: vi.fn(), agentDefinitions: vi.fn(), saveAgents: vi.fn() }));
vi.mock("../api/tga3-config", () => ({ tga3ConfigApi: mocks }));
import { SolversPage } from "./SolversPage";

const agentConfig = {
  schema_version: 1,
  agents: {
    supervisor: { display_name: "Supervisor", role: "supervisor", runtime: "openai_agents", provider_id: "deepseek", model_id: "deepseek-v4-pro", protocol: "openai_chat_completions", max_turns_per_cycle: 8, system_prompt: "supervise" },
    "worker-claude": { display_name: "Claude Worker", role: "worker", runtime: "claude_agent", provider_id: "deepseek", model_id: "deepseek-v4-pro", protocol: "anthropic", max_turns_per_cycle: 12, system_prompt: "claude work" },
    "worker-openai": { display_name: "OpenAI Worker", role: "worker", runtime: "openai_agents", provider_id: "deepseek", model_id: "deepseek-v4-pro", protocol: "openai_chat_completions", max_turns_per_cycle: 12, system_prompt: "openai work" },
    reporter: { display_name: "Reporter", role: "reporter", runtime: "openai_agents", provider_id: "deepseek", model_id: "deepseek-v4-pro", protocol: "openai_chat_completions", max_turns_per_cycle: 8, system_prompt: "report" },
  },
} as const;

describe("SolversPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.agents.mockResolvedValue(structuredClone(agentConfig));
    mocks.models.mockResolvedValue({ schema_version: 1, providers: [{ id: "deepseek", name: "DeepSeek", protocols: ["openai_chat_completions", "anthropic"], api_keys: [], selected_api_key_id: "", models: [{ id: "deepseek-v4-pro", name: "deepseek-v4-pro", max_output_tokens: 8192, timeout_seconds: 120 }] }] });
    mocks.agentDefinitions.mockResolvedValue(Object.entries(agentConfig.agents).map(([id, agent]) => ({ ...agent, id, tools: ["skills.list", "skills.read"], image: agent.role === "worker" ? `tga3-${id}:local` : null, image_health: agent.role === "worker" ? { status: "healthy", available: true, detail: "healthy" } : null })));
    mocks.saveAgents.mockImplementation(async (value) => value);
  });

  it("uses the static team graph instead of an Agent list", async () => {
    render(<SolversPage />);
    const graph = await screen.findByRole("region", { name: "Solver 团队图" });
    expect(within(graph).getByRole("button", { name: "配置 OpenAI Worker" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent 列表")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("OpenAI Worker Solver 配置")).not.toBeInTheDocument();
  });

  it("focuses one Agent and opens all configuration sections in one drawer", async () => {
    const user = userEvent.setup();
    render(<SolversPage />);
    await user.click(await screen.findByRole("button", { name: "配置 OpenAI Worker" }));
    const panel = screen.getByLabelText("OpenAI Worker Solver 配置");
    for (const heading of ["概览", "模型", "System Prompt", "工具与镜像"]) expect(within(panel).getByRole("heading", { name: heading })).toBeInTheDocument();
    expect(document.querySelector(".lane-supervisor")).toHaveAttribute("data-dimmed", "true");
    expect(document.querySelector(".lane-worker-openai")).toHaveAttribute("data-dimmed", "false");
    expect(document.querySelector(".lane-supervisor .solver-team-agent")).toHaveAttribute("data-dimmed", "true");
    expect(document.querySelector(".lane-worker-openai .solver-team-agent")).toHaveAttribute("data-dimmed", "false");
    expect(document.querySelector(".lane-worker-openai .model")).toHaveAttribute("data-dimmed", "true");
    const prompt = within(panel).getByLabelText("OpenAI Worker System Prompt");
    await user.clear(prompt); await user.type(prompt, "updated prompt");
    await user.click(within(panel).getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(mocks.saveAgents).toHaveBeenCalledWith(expect.objectContaining({ agents: expect.objectContaining({ "worker-openai": expect.objectContaining({ system_prompt: "updated prompt" }) }) })));
  });
});
