import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CapabilitiesPage } from "./SettingsPages";

const capabilities = vi.fn();
const health = vi.fn();
const importMCP = vi.fn();
const deleteMCP = vi.fn();
const setMCPEnabled = vi.fn();
const mcpServers = vi.fn();

vi.mock("../api/capabilities", () => ({
  fetchCapabilities: (...args: unknown[]) => capabilities(...args),
  fetchMCPHealth: (...args: unknown[]) => health(...args),
}));
vi.mock("../api/tasks", () => ({
  fetchPromptSettings: vi.fn(),
  fetchSkillSettings: vi.fn(),
  getLLMSettings: vi.fn(),
  updateLLMSettings: vi.fn(),
  verifyLLMSettings: vi.fn(),
}));
vi.mock("../runtime/api-v2", () => ({
  runtimeApi: {
    importMCP: (...args: unknown[]) => importMCP(...args),
    deleteMCP: (...args: unknown[]) => deleteMCP(...args),
    setMCPEnabled: (...args: unknown[]) => setMCPEnabled(...args),
    refreshMCP: vi.fn(),
    mcpServers: (...args: unknown[]) => mcpServers(...args),
  },
}));

describe("CapabilitiesPage MCP import", () => {
  beforeEach(() => {
    capabilities.mockReset();
    health.mockReset();
    importMCP.mockReset();
    deleteMCP.mockReset();
    setMCPEnabled.mockReset();
    mcpServers.mockReset();
    capabilities.mockResolvedValue({ capabilities: [], tools: { availability: "healthy", tools: [] } });
    health.mockResolvedValue({ configured: true, records: [] });
    mcpServers.mockResolvedValue({ servers: [] });
    importMCP.mockResolvedValue({
      server_id: "demo",
      image: "demo-mcp:latest",
      source_type: "docker-image",
      config_path: "config/mcp.json",
      config_action: "created",
      catalog: { configured: true, records: [{ server: "demo", discovered: true, tools: 2 }] },
    });
    deleteMCP.mockResolvedValue({ deleted: true, server_id: "demo", image_deleted: false, catalog: { configured: true, records: [] } });
    setMCPEnabled.mockResolvedValue({ server_id: "demo", enabled: false, catalog: { configured: true, records: [{ server: "demo", enabled: false, discovered: false, tools: 0 }] } });
  });

  it("imports a dropped image and reports the discovered tool count", async () => {
    render(<CapabilitiesPage />);
    await waitFor(() => expect(capabilities).toHaveBeenCalled());
    const file = new File(["docker archive"], "demo.tar", { type: "application/x-tar" });
    fireEvent.drop(screen.getByRole("button", { name: /Drop an MCP image file here/ }), {
      dataTransfer: { files: [file] },
    });
    await waitFor(() => expect(importMCP).toHaveBeenCalledWith(file));
    expect(await screen.findByText(/2 tools discovered/)).toBeInTheDocument();
  });

  it("groups tools by MCP service and supports disable plus confirmed deletion", async () => {
    capabilities.mockResolvedValue({
      capabilities: [],
      tools: {
        availability: "healthy",
        tools: [
          { tool_id: "demo", provider_name: "mcp__demo__scan", risk: "active", methods: [{ name: "scan" }] },
          { tool_id: "demo", provider_name: "mcp__demo__status", risk: "passive", methods: [{ name: "status" }] },
        ],
      },
    });
    health.mockResolvedValue({ configured: true, records: [{ server: "demo", enabled: true, discovered: true, tools: 2 }] });
    render(<CapabilitiesPage />);
    const toggle = await screen.findByRole("button", { name: /demo.*2 tools/i });
    expect(screen.queryByText("mcp__demo__scan")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText("mcp__demo__scan")).toBeInTheDocument();
    expect(screen.getByText("mcp__demo__status")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    await waitFor(() => expect(setMCPEnabled).toHaveBeenCalledWith("demo", false));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("dialog", { name: "Delete MCP service?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete from config" }));
    await waitFor(() => expect(deleteMCP).toHaveBeenCalledWith("demo"));
  });

  it("offers enable for a disabled configured MCP service", async () => {
    health.mockResolvedValue({ configured: true, records: [{ server: "bridge", enabled: false, discovered: false, tools: 0 }] });
    setMCPEnabled.mockResolvedValue({ server_id: "bridge", enabled: true, catalog: { configured: true, records: [{ server: "bridge", enabled: true, discovered: false, tools: 0 }] } });
    render(<CapabilitiesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Enable" }));
    await waitFor(() => expect(setMCPEnabled).toHaveBeenCalledWith("bridge", true));
  });

  it("keeps the capability catalog visible when the managed-server request fails", async () => {
    mcpServers.mockRejectedValue(new Error("MCP_CONFIG_INVALID: MCP configuration is invalid"));

    render(<CapabilitiesPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("servers: MCP_CONFIG_INVALID: MCP configuration is invalid");
    expect(screen.getByText(/0 configured services.*0 discovered tools/)).toBeInTheDocument();
    expect(screen.queryByText(/Loading configured MCP catalog/)).toBeNull();
    expect(screen.getByText("healthy")).toBeInTheDocument();
  });

  it("leaves the loading state when the capability request itself fails", async () => {
    capabilities.mockRejectedValue(new Error("capability endpoint unavailable"));

    render(<CapabilitiesPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("capabilities: capability endpoint unavailable");
    expect(screen.getByText("MCP catalog could not be loaded; other capability data remains available.")).toBeInTheDocument();
    expect(screen.queryByText(/Loading configured MCP catalog/)).toBeNull();
    expect(screen.getByText("unavailable")).toBeInTheDocument();
  });
});
