import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchProviderCatalog: vi.fn(), createModelProvider: vi.fn(), deleteModelProvider: vi.fn(),
  discoverProviderModels: vi.fn(), updateProviderEndpoints: vi.fn(), setProviderModelsEnabled: vi.fn(),
  addProviderAPIKey: vi.fn(), selectProviderAPIKey: vi.fn(),
}));
vi.mock("../api/tga3-models", async (original) => ({ ...await original<typeof import("../api/tga3-models")>(), ...mocks }));
import { ModelsPage } from "./ModelsPage";

const model = (name: string, enabled: boolean) => ({ id: name, name, enabled, available: true, max_output_tokens: 8192, timeout_seconds: 180 });
const provider = {
  id: "gateway", name: "团队网关", preset_id: "custom", built_in: false,
  endpoints: {
    openai_responses: { base_url: "https://api.test/compatible-mode/v1", models: [model("qwen-disabled", false), model("qwen-enabled", true)] },
    anthropic: { base_url: "https://api.test/apps/anthropic", models: [model("claude-model", true)] },
  }, selected_api_key_id: "primary",
  api_keys: [{ id: "primary", label: "Production", masked: "sk-••••1234", selected: true, configured: true },
    { id: "backup", label: "Backup", masked: "sk-••••5678", selected: false, configured: true }],
};
const catalog = { schema_version: 1, presets: [{ id: "example", name: "预设服务", endpoints: provider.endpoints, default_protocol: "openai_responses" }], providers: [provider] };

describe("ModelsPage protocol endpoints", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchProviderCatalog.mockImplementation(async () => structuredClone(catalog));
    mocks.createModelProvider.mockResolvedValue({ provider, warnings: [] });
    mocks.selectProviderAPIKey.mockResolvedValue({ warnings: [] });
    mocks.addProviderAPIKey.mockResolvedValue({ warnings: [] });
  });

  it("creates one supplier with independently entered protocol URLs", async () => {
    render(<ModelsPage />);
    await screen.findByRole("heading", { name: "团队网关" });
    fireEvent.click(screen.getByRole("button", { name: "添加供应商" }));
    fireEvent.change(screen.getByLabelText("供应商名称"), { target: { value: "新网关" } });
    fireEvent.change(screen.getByLabelText("OpenAI Responses API 地址"), { target: { value: "https://api.test/compatible-mode/v1" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Anthropic Messages" }));
    fireEvent.change(screen.getByLabelText("Anthropic Messages API 地址"), { target: { value: "https://api.test/apps/anthropic" } });
    expect(screen.getByLabelText("API 密钥")).toHaveAttribute("type", "password");
    fireEvent.change(screen.getByLabelText("API 密钥"), { target: { value: "test-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "保存供应商" }));
    await waitFor(() => expect(mocks.createModelProvider).toHaveBeenCalledWith({
      preset_id: "custom", name: "新网关", api_key: "test-secret", endpoints: {
        openai_responses: { base_url: "https://api.test/compatible-mode/v1", models: [] },
        anthropic: { base_url: "https://api.test/apps/anthropic", models: [] },
      },
    }));
    await waitFor(() => expect(screen.queryByDisplayValue("test-secret")).toBeNull());
  });

  it("rejects a provider without any protocol endpoint", async () => {
    render(<ModelsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "添加供应商" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "OpenAI Responses" }));
    fireEvent.submit(screen.getByRole("button", { name: "保存供应商" }).closest("form")!);
    expect(await screen.findByRole("status")).toHaveTextContent("至少填写一个协议端点");
    expect(mocks.createModelProvider).not.toHaveBeenCalled();
  });

  it("puts enabled models first and reorders after toggling while preserving search", async () => {
    let current = structuredClone(catalog);
    mocks.fetchProviderCatalog.mockImplementation(async () => structuredClone(current));
    mocks.setProviderModelsEnabled.mockImplementation(async (_id, _protocol, ids, enabled) => {
      current = structuredClone(current);
      current.providers[0].endpoints.openai_responses.models.forEach((model) => {
        if (ids.includes(model.id)) model.enabled = enabled;
      });
    });
    render(<ModelsPage />);
    const responses = await screen.findByRole("region", { name: "OpenAI Responses 模型" });
    expect(within(responses).getAllByRole("switch").map((item) => item.getAttribute("aria-label"))).toEqual([
      "启用 qwen-enabled", "启用 qwen-disabled",
    ]);
    fireEvent.change(within(responses).getByRole("searchbox"), { target: { value: "qwen" } });
    fireEvent.click(within(responses).getByRole("switch", { name: "启用 qwen-disabled" }));
    await waitFor(() => expect(within(responses).getAllByRole("switch")[0]).toHaveAttribute("aria-checked", "true"));
    await waitFor(() => expect(within(responses).getAllByRole("switch")[0]).toHaveAttribute("aria-label", "启用 qwen-disabled"));
    expect(within(responses).getByRole("searchbox")).toHaveValue("qwen");
    fireEvent.click(within(responses).getByRole("switch", { name: "启用 qwen-disabled" }));
    await waitFor(() => expect(within(responses).getAllByRole("switch")[0]).toHaveAttribute("aria-label", "启用 qwen-enabled"));
    expect(within(responses).getAllByRole("switch")[1]).toHaveAttribute("aria-checked", "false");
  });

  it("collapses models, searches them, and toggles only the selected endpoint model", async () => {
    render(<ModelsPage />);
    const responses = await screen.findByRole("region", { name: "OpenAI Responses 模型" });
    const toggle = within(responses).getByRole("button", { expanded: true });
    fireEvent.click(toggle);
    expect(within(responses).queryByRole("searchbox")).toBeNull();
    fireEvent.click(toggle);
    fireEvent.change(within(responses).getByRole("searchbox"), { target: { value: "DISABLED" } });
    expect(within(responses).queryByRole("switch", { name: "启用 qwen-enabled" })).toBeNull();
    const modelSwitch = within(responses).getByRole("switch", { name: "启用 qwen-disabled" });
    expect(modelSwitch).toHaveAttribute("aria-checked", "false");
    fireEvent.click(modelSwitch);
    await waitFor(() => expect(mocks.setProviderModelsEnabled).toHaveBeenCalledWith("gateway", "openai_responses", ["qwen-disabled"], true));
    fireEvent.click(screen.getByRole("tab", { name: "Anthropic Messages" }));
    expect(within(screen.getByRole("region", { name: "Anthropic Messages 模型" })).getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("refreshes and bulk disables only the targeted protocol endpoint", async () => {
    render(<ModelsPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "Anthropic Messages" }));
    const anthropic = await screen.findByRole("region", { name: "Anthropic Messages 模型" });
    fireEvent.click(within(anthropic).getByRole("button", { name: "读取可访问模型" }));
    await waitFor(() => expect(mocks.discoverProviderModels).toHaveBeenCalledWith("gateway", "anthropic"));
    await waitFor(() => expect(within(anthropic).getByRole("button", { name: "全部关闭" })).toBeEnabled());
    fireEvent.click(within(anthropic).getByRole("button", { name: "全部关闭" }));
    await waitFor(() => expect(mocks.setProviderModelsEnabled).toHaveBeenCalledWith("gateway", "anthropic", ["claude-model"], false));
  });

  it("edits explicit endpoint URLs and preserves each endpoint's model choices", async () => {
    render(<ModelsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑协议端点" }));
    fireEvent.change(screen.getByLabelText("OpenAI Responses API 地址"), { target: { value: "https://new.test/api/v2" } });
    fireEvent.click(screen.getByRole("button", { name: "保存端点" }));
    await waitFor(() => expect(mocks.updateProviderEndpoints).toHaveBeenCalledWith("gateway", {
      ...provider.endpoints, openai_responses: { ...provider.endpoints.openai_responses, base_url: "https://new.test/api/v2" },
    }));
  });

  it("shares masked keys and lets the user choose another key", async () => {
    render(<ModelsPage />);
    expect(await screen.findByText("sk-••••1234")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Backup/ }));
    await waitFor(() => expect(mocks.selectProviderAPIKey).toHaveBeenCalledWith("gateway", "backup"));
  });
});
