import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CapabilitiesPage } from "./ToolsPage";

const capabilities = vi.fn();
const health = vi.fn();
const importMCP = vi.fn();
const deleteMCPServer = vi.fn();
const updateMCPServer = vi.fn();
const mcpServers = vi.fn();

vi.mock("../api/capabilities", () => ({
  fetchCapabilities: (...args: unknown[]) => capabilities(...args),
  fetchMCPHealth: (...args: unknown[]) => health(...args),
}));
vi.mock("../api/tasks", () => ({
  fetchSkillSettings: vi.fn(),
  getLLMSettings: vi.fn(),
  updateLLMSettings: vi.fn(),
  verifyLLMSettings: vi.fn(),
}));
vi.mock("../runtime/api-v2", () => ({
  runtimeApi: {
    importMCP: (...args: unknown[]) => importMCP(...args),
    deleteMCPServer: (...args: unknown[]) => deleteMCPServer(...args),
    updateMCPServer: (...args: unknown[]) => updateMCPServer(...args),
    refreshMCPServer: vi.fn(),
    mcpServers: (...args: unknown[]) => mcpServers(...args),
  },
}));

describe("CapabilitiesPage MCP import", () => {
  beforeEach(() => {
    capabilities.mockReset();
    health.mockReset();
    importMCP.mockReset();
    deleteMCPServer.mockReset();
    updateMCPServer.mockReset();
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
    deleteMCPServer.mockResolvedValue({ deleted: true, server_id: "demo", image_deleted: false });
    updateMCPServer.mockResolvedValue({ server: { id: "demo", config: { enabled: false }, status: { server: "demo", enabled: false, discovered: false, tools: 0 } } });
  });

  it("imports a dropped image and reports the discovered tool count", async () => {
    render(<CapabilitiesPage />);
    await waitFor(() => expect(capabilities).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "MCP Servers" }));
    const file = new File(["docker archive"], "demo.tar", { type: "application/x-tar" });
    fireEvent.drop(screen.getByRole("button", { name: /将 MCP 镜像归档拖到这里/ }), {
      dataTransfer: { files: [file] },
    });
    await waitFor(() => expect(importMCP).toHaveBeenCalledWith(file));
    expect(await screen.findByText(/已发现 2 个工具/)).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "MCP Servers" }));
    const toggle = await screen.findByRole("button", { name: /demo.*2 个工具/i });
    expect(screen.queryByText("mcp__demo__scan")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText("mcp__demo__scan")).toBeInTheDocument();
    expect(screen.getByText("mcp__demo__status")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停用" }));
    await waitFor(() => expect(updateMCPServer).toHaveBeenCalledWith("demo", { enabled: false }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(screen.getByRole("dialog", { name: "删除 MCP 服务？" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "从配置中删除" }));
    await waitFor(() => expect(deleteMCPServer).toHaveBeenCalledWith("demo"));
  });

  it("offers enable for a disabled configured MCP service", async () => {
    health.mockResolvedValue({ configured: true, records: [{ server: "bridge", enabled: false, discovered: false, tools: 0 }] });
    updateMCPServer.mockResolvedValue({ server: { id: "bridge", config: { enabled: true }, status: { server: "bridge", enabled: true, discovered: false, tools: 0 } } });
    render(<CapabilitiesPage />);
    fireEvent.click(screen.getByRole("button", { name: "MCP Servers" }));
    fireEvent.click(await screen.findByRole("button", { name: "启用" }));
    await waitFor(() => expect(updateMCPServer).toHaveBeenCalledWith("bridge", { enabled: true }));
  });

  it("keeps the capability catalog visible when the managed-server request fails", async () => {
    mcpServers.mockRejectedValue(new Error("MCP_CONFIG_INVALID: MCP configuration is invalid"));

    render(<CapabilitiesPage />);
    fireEvent.click(screen.getByRole("button", { name: "MCP Servers" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("servers: MCP_CONFIG_INVALID: MCP configuration is invalid");
    expect(screen.getByText(/已配置 0 个服务，发现 0 个工具/)).toBeInTheDocument();
    expect(screen.queryByText(/正在读取已配置的 MCP 工具目录/)).toBeNull();
    expect(screen.getByText("健康")).toBeInTheDocument();
  });

  it("leaves the loading state when the capability request itself fails", async () => {
    capabilities.mockRejectedValue(new Error("capability endpoint unavailable"));

    render(<CapabilitiesPage />);
    fireEvent.click(screen.getByRole("button", { name: "MCP Servers" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("capabilities: capability endpoint unavailable");
    expect(screen.getByText("MCP 工具目录暂时无法读取，其他运行时能力仍可使用。")).toBeInTheDocument();
    expect(screen.queryByText(/正在读取已配置的 MCP 工具目录/)).toBeNull();
    expect(screen.getByText("不可用")).toBeInTheDocument();
  });
});
