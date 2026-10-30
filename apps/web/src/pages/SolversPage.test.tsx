import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ agents: vi.fn(), models: vi.fn(), agentDefinitions: vi.fn(), saveAgents: vi.fn() }));
vi.mock("../api/tga3-config", () => ({ tga3ConfigApi: mocks }));
import { SolversPage } from "./SolversPage";

const agentConfig = {
  schema_version: 1,
  agents: {
    "worker-claude": { display_name: "Claude Worker", role: "worker", runtime: "claude_cli", provider_id: "deepseek", model_id: "deepseek-v4-pro", protocol: "anthropic", max_turns_per_cycle: 12, system_prompt: "claude work" },
    "worker-openai": { display_name: "OpenAI Worker", role: "worker", runtime: "codex_cli", provider_id: "deepseek", model_id: "deepseek-v4-pro", protocol: "openai_responses", max_turns_per_cycle: 12, system_prompt: "openai work" },
    reporter: { display_name: "Reporter", role: "reporter", runtime: "codex_cli", provider_id: "deepseek", model_id: "deepseek-v4-pro", protocol: "openai_responses", max_turns_per_cycle: 8, system_prompt: "report" },
  },
} as const;

describe("SolversPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.agents.mockResolvedValue(structuredClone(agentConfig));
    const model = (id: string, enabled = true) => ({ id, name: id, enabled, available: true, max_output_tokens: 8192, timeout_seconds: 120 });
    mocks.models.mockResolvedValue({ schema_version: 1, providers: [{ id: "deepseek", name: "DeepSeek", api_keys: [], selected_api_key_id: "", endpoints: {
      openai_responses: { base_url: "https://api.test/responses/v1", models: [model("deepseek-v4-pro"), model("responses-only"), model("disabled-model", false)] },
      anthropic: { base_url: "https://api.test/anthropic", models: [model("deepseek-v4-pro"), model("anthropic-only")] },
      openai_chat_completions: { base_url: "https://api.test/chat/v1", models: [model("chat-only")] },
    } }] });
    mocks.agentDefinitions.mockResolvedValue(Object.entries(agentConfig.agents).map(([id, agent]) => ({ ...agent, id, tools: ["blackboard.sync", "blackboard.publish", "artifact.register"], image: agent.role === "worker" ? `tga3-${id}:local` : null, image_health: agent.role === "worker" ? { status: "healthy", available: true, detail: "healthy" } : null })));
    mocks.saveAgents.mockImplementation(async (value) => value);
  });

  it("shows the task phases and opens the selected Agent configuration", async () => {
    render(<SolversPage />);
    const graph = await screen.findByRole("region", { name: "Solver 团队图" });
    expect(within(graph).getByRole("button", { name: "配置 OpenAI Worker" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("OpenAI Worker Solver 配置")).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent 列表")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "运行与模型" })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps prompt edits when switching agents and saves the whole configuration", async () => {
    const user = userEvent.setup();
    render(<SolversPage />);
    await user.click(await screen.findByRole("button", { name: "配置 OpenAI Worker" }));
    await user.click(screen.getByRole("tab", { name: "系统提示词" }));
    const prompt = screen.getByLabelText("OpenAI Worker System Prompt");
    await user.clear(prompt); await user.type(prompt, "updated prompt");
    await user.click(screen.getByRole("button", { name: "配置 Claude Worker" }));
    await user.click(screen.getByRole("button", { name: "配置 OpenAI Worker" }));
    const panel = screen.getByLabelText("OpenAI Worker Solver 配置");
    expect(within(panel).getByLabelText("OpenAI Worker System Prompt")).toHaveValue("updated prompt");
    expect(within(panel).getByRole("status")).toHaveTextContent("有未保存的更改");
    await user.click(within(panel).getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(mocks.saveAgents).toHaveBeenCalledWith(expect.objectContaining({ agents: expect.objectContaining({ "worker-openai": expect.objectContaining({ system_prompt: "updated prompt" }) }) })));
  });

  it("only offers enabled models from the CLI's endpoint without protocol suffixes", async () => {
    const user = userEvent.setup();
    render(<SolversPage />);
    await user.click(await screen.findByRole("button", { name: "配置 OpenAI Worker" }));
    let modelSelect = within(screen.getByLabelText("OpenAI Worker Solver 配置")).getByRole("combobox");
    expect(within(modelSelect).getByRole("option", { name: "DeepSeek / responses-only" })).toBeInTheDocument();
    expect(within(modelSelect).queryByRole("option", { name: /anthropic-only|disabled-model|chat-only|Responses/ })).toBeNull();
    await user.selectOptions(modelSelect, "deepseek::responses-only::openai_responses");
    await user.click(within(screen.getByLabelText("OpenAI Worker Solver 配置")).getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(mocks.saveAgents).toHaveBeenCalledWith(expect.objectContaining({ agents: expect.objectContaining({ "worker-openai": expect.objectContaining({ model_id: "responses-only", protocol: "openai_responses" }) }) })));
    await user.click(screen.getByRole("button", { name: "配置 Claude Worker" }));
    modelSelect = within(screen.getByLabelText("Claude Worker Solver 配置")).getByRole("combobox");
    expect(within(modelSelect).getByRole("option", { name: "DeepSeek / anthropic-only" })).toBeInTheDocument();
    expect(within(modelSelect).queryByRole("option", { name: /responses-only|disabled-model|chat-only|Messages/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "配置 Reporter" }));
    modelSelect = within(screen.getByLabelText("Reporter Solver 配置")).getByRole("combobox");
    expect(within(modelSelect).getByRole("option", { name: "DeepSeek / responses-only" })).toBeInTheDocument();
  });

  it("saves each Worker's blackboard interval without a fixed turn limit", async () => {
    const user = userEvent.setup();
    render(<SolversPage />);
    await user.click(await screen.findByRole("button", { name: "配置 Claude Worker" }));
    const panel = screen.getByLabelText("Claude Worker Solver 配置");
    expect(within(panel).queryByText("每次执行最大轮数")).not.toBeInTheDocument();
    const interval = within(panel).getByLabelText("黑板同步间隔（秒）");
    await user.clear(interval);
    await user.type(interval, "120");
    await user.click(within(panel).getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(mocks.saveAgents).toHaveBeenCalledWith(expect.objectContaining({
      agents: expect.objectContaining({ "worker-claude": expect.objectContaining({ blackboard_interval_seconds: 120 }) }),
    })));
  });
});
