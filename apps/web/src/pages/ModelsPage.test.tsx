import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getLLMSettings: vi.fn(),
  updateLLMSettings: vi.fn(),
  verifyLLMSettings: vi.fn(),
}));

vi.mock("../api/tasks", async (original) => ({ ...await original<typeof import("../api/tasks")>(), ...mocks }));
vi.mock("../api/capabilities", () => ({ fetchCapabilities: vi.fn(), fetchMCPHealth: vi.fn() }));
vi.mock("../runtime/api-v2", () => ({ runtimeApi: {} }));

import { ModelsPage } from "./SettingsPages";

describe("ModelsPage browser configuration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getLLMSettings.mockResolvedValue({
      configured: false,
      base_url: "",
      model: "",
      api_key_set: false,
      supports_vision: null,
      max_output_tokens: 1024, timeout_seconds: 60, temperature: 0.2, reasoning_mode: "auto",
    });
    mocks.updateLLMSettings.mockResolvedValue({
      configured: true,
      base_url: "https://provider.example/v1",
      model: "tool-model",
      api_key_set: true,
      browser_configured: true,
      supports_vision: true,
      max_output_tokens: 1024, timeout_seconds: 60, temperature: 0.2, reasoning_mode: "auto",
    });
  });

  it("submits a write-only API key and clears it after save", async () => {
    const onConfiguredChange = vi.fn();
    render(<ModelsPage onConfiguredChange={onConfiguredChange} />);

    const baseUrl = await screen.findByLabelText("Provider Base URL");
    fireEvent.change(baseUrl, { target: { value: "https://provider.example/v1" } });
    fireEvent.change(screen.getByLabelText("模型 ID"), { target: { value: "tool-model" } });
    const key = screen.getByLabelText("API Key");
    expect(key).toHaveAttribute("type", "password");
    fireEvent.change(key, { target: { value: "browser-secret" } });
    fireEvent.change(screen.getByLabelText("视觉输入"), { target: { value: "true" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => expect(mocks.updateLLMSettings).toHaveBeenCalledWith({
      base_url: "https://provider.example/v1",
      model: "tool-model",
      api_key: "browser-secret",
      supports_vision: true,
      max_output_tokens: 1024,
      timeout_seconds: 60,
      temperature: 0.2,
      reasoning_mode: "auto",
    }));
    expect(key).toHaveValue("");
    expect(onConfiguredChange).toHaveBeenCalledWith(true);
    expect(screen.getByRole("status")).toHaveTextContent("API Key 不会回显");
  });

  it("never places the saved key into the input", async () => {
    mocks.getLLMSettings.mockResolvedValue({
      configured: true,
      base_url: "https://provider.example/v1",
      model: "tool-model",
      api_key_set: true,
      supports_vision: false,
      max_output_tokens: 4096, timeout_seconds: 90, temperature: 0.4, reasoning_mode: "enabled",
    });
    render(<ModelsPage />);

    const key = await screen.findByLabelText("API Key");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("placeholder", "已保存，留空表示不修改");
  });
});
