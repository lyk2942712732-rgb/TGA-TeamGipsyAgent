import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchProviderCatalog: vi.fn(),
  createModelProvider: vi.fn(),
  deleteModelProvider: vi.fn(),
  discoverProviderModels: vi.fn(),
  updateProviderEndpoint: vi.fn(),
  addProviderAPIKey: vi.fn(),
  selectProviderAPIKey: vi.fn(),
  verifyProviderModel: vi.fn(),
}));

vi.mock("../api/tasks", async (original) => ({ ...await original<typeof import("../api/tasks")>(), ...mocks }));

import { ModelsPage } from "./ModelsPage";

const emptyCatalog = {
  schema_version: 1 as const,
  presets: [
    { id: "openai", name: "OpenAI", base_url: "https://api.openai.com", protocol: "openai_responses" as const },
    { id: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com", protocol: "anthropic" as const },
  ],
  providers: [],
};

const configuredCatalog = {
  ...emptyCatalog,
  providers: [{
    id: "provider_deepseek", name: "DeepSeek", preset_id: "deepseek",
    built_in: false, protocol: "anthropic" as const,
    base_url: "https://api.deepseek.com", selected_api_key_id: "key_1",
    models: [{
      id: "model_1", name: "deepseek-chat", max_output_tokens: 1024,
      timeout_seconds: 60, temperature: 0.2, reasoning_mode: "auto" as const,
      verification_status: "verified" as const,
      verification: { status: "verified" as const, capabilities: { tool_calling: true } },
    }],
    api_keys: [
      { id: "key_1", label: "Production", masked: "••••••••1234", selected: true },
      { id: "key_2", label: "Backup", masked: "••••••••5678", selected: false },
    ],
  }],
};

describe("ModelsPage provider catalog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchProviderCatalog.mockResolvedValue(emptyCatalog);
    mocks.createModelProvider.mockResolvedValue({ provider: configuredCatalog.providers[0] });
    mocks.deleteModelProvider.mockResolvedValue(undefined);
    mocks.discoverProviderModels.mockResolvedValue({ provider_id: "provider_deepseek", count: 1, models: configuredCatalog.providers[0].models });
    mocks.updateProviderEndpoint.mockResolvedValue({ provider: configuredCatalog.providers[0] });
    mocks.selectProviderAPIKey.mockResolvedValue({ provider: configuredCatalog.providers[0] });
  });

  it("prefills an official URL and lets the backend discover models from a write-only API key", async () => {
    render(<ModelsPage />);
    await screen.findByText("还没有供应商");
    fireEvent.click(screen.getByRole("button", { name: "添加供应商" }));
    fireEvent.change(screen.getByLabelText("供应商类型"), { target: { value: "deepseek" } });
    expect(screen.getByLabelText("API URL")).toHaveValue("https://api.deepseek.com");
    const key = screen.getByLabelText("API 密钥");
    expect(key).toHaveAttribute("type", "password");
    fireEvent.change(key, { target: { value: "secret-key-value" } });
    fireEvent.click(screen.getByRole("button", { name: "保存供应商" }));

    await waitFor(() => expect(mocks.createModelProvider).toHaveBeenCalledWith({
      preset_id: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com",
      protocol: "anthropic",
      api_key: "secret-key-value",
    }));
    expect(screen.queryByDisplayValue("secret-key-value")).toBeNull();
  });

  it("shows only masked keys and selects a key by clicking its detail row", async () => {
    mocks.fetchProviderCatalog.mockResolvedValue(configuredCatalog);
    render(<ModelsPage />);

    expect(await screen.findByText("••••••••1234")).toBeInTheDocument();
    expect(screen.queryByText("secret-key-value")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Backup/ }));
    await waitFor(() => expect(mocks.selectProviderAPIKey).toHaveBeenCalledWith("provider_deepseek", "key_2"));
    expect(screen.getByLabelText("添加 API 密钥")).toHaveAttribute("type", "password");
    expect(screen.queryByLabelText("添加模型")).toBeNull();
  });

  it("refreshes models from the current API URL and selected key", async () => {
    mocks.fetchProviderCatalog.mockResolvedValue(configuredCatalog);
    render(<ModelsPage />);

    await screen.findByText("deepseek-chat");
    fireEvent.click(screen.getByRole("button", { name: "读取可访问模型" }));
    await waitFor(() => expect(mocks.discoverProviderModels).toHaveBeenCalledWith("provider_deepseek"));
  });

  it("deletes a user-added provider after confirmation", async () => {
    mocks.fetchProviderCatalog
      .mockResolvedValueOnce(configuredCatalog)
      .mockResolvedValueOnce(emptyCatalog);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ModelsPage />);

    await screen.findByText("deepseek-chat");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mocks.deleteModelProvider).toHaveBeenCalledWith("provider_deepseek"));
  });
});
