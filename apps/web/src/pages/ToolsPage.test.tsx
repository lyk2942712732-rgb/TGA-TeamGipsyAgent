import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requestJson: vi.fn(),
  toolHealth: vi.fn(),
  updateMCPServer: vi.fn(),
  deleteMCPServer: vi.fn(),
  mcpServers: vi.fn(),
}));

vi.mock("../api/client", async (original) => ({
  ...await original<typeof import("../api/client")>(),
  requestJson: (...args: unknown[]) => mocks.requestJson(...args),
}));
vi.mock("../runtime/api-v2", () => ({
  runtimeApi: {
    toolHealth: (...args: unknown[]) => mocks.toolHealth(...args),
    mcpServers: (...args: unknown[]) => mocks.mcpServers(...args),
    updateMCPServer: (...args: unknown[]) => mocks.updateMCPServer(...args),
    deleteMCPServer: (...args: unknown[]) => mocks.deleteMCPServer(...args),
  },
}));
vi.mock("../components/mcp/MCPWizard", () => ({ MCPWizard: () => <div>mcp wizard</div> }));

import { CapabilitiesPage } from "./ToolsPage";

const hostCapability = {
  id: "artifact.inspect",
  display_name: "Inspect artifact",
  category: "artifact",
  description: "Read an immutable task artifact.",
  allowed_roles: ["supervisor", "worker", "reviewer", "reporter"],
  risk: "passive",
  input_schema: { properties: { artifact_id: {}, query: {} }, required: ["artifact_id"] },
  output_schema: {},
  handler_key: "artifact.inspect",
  handler_status: "ready",
  assigned_solver_count: 1,
  assigned_solver_ids: ["ctf-pwn-solver"],
};

const kaliCapability = {
  id: "kali.exec",
  display_name: "Kali command execution",
  description: "Execute an allowlisted program.",
  risk: "active",
  input_schema: { properties: { executable: {}, argv: {}, cwd: {} } },
  assigned_solver_count: 1,
  assigned_solver_ids: ["ctf-pwn-solver"],
  profile_ids: ["ctf-pwn-v1"],
};

const kaliProfile = {
  id: "ctf-pwn-v1",
  display_name: "CTF pwn",
  image_name: "tga/kali-ctf-pwn",
  image_tag: "2026.08",
  image_digest: "sha256:" + "a".repeat(64),
  image: "tga/kali-ctf-pwn:2026.08",
  tools: [{ name: "gdb", executable: "gdb", version: "16.3", category: "pwn" }],
  supported_capabilities: ["kali.exec", "kali.session"],
  allowed_executables: ["gdb"],
  session_executables: ["gdb"],
  network_mode: "disabled",
  input_mount: "read_only",
  scratch_mount: "private_read_write",
  shared_artifact_mount: "read_only",
  limits: { cpu_cores: 2, memory_mb: 4096, timeout_seconds: 300, max_processes: 256 },
  enabled: true,
  assigned_solver_count: 1,
  assigned_solver_ids: ["ctf-pwn-solver"],
};

const mcpRecord = {
  server: "binwalk", configured: true, enabled: false, reachable: false,
  discovered: false, tools: 0, transport: "stdio", image: "binwalk-mcp:latest",
  endpoint: null, error: null, protocol_version: "",
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><CapabilitiesPage /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.requestJson.mockImplementation((path: string) => {
    if (path === "/api/v2/capabilities/host") return Promise.resolve({ items: [hostCapability], total: 1 });
    if (path === "/api/v2/capabilities/kali") return Promise.resolve({ items: [kaliCapability], total: 1 });
    if (path === "/api/v2/kali/profiles") return Promise.resolve({ items: [kaliProfile], total: 1 });
    return Promise.reject(new Error(`unexpected path ${path}`));
  });
  mocks.toolHealth.mockResolvedValue({ configured: true, records: [mcpRecord] });
  mocks.mcpServers.mockResolvedValue({ servers: [{ id: "binwalk", config: {} }] });
});

describe("Tools & MCP", () => {
  it("renders three authoritative management tabs", async () => {
    renderPage();
    expect(screen.getByRole("tab", { name: /Host/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Kali/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /MCP Servers/ })).toBeInTheDocument();
    expect(await screen.findAllByText("artifact.inspect")).not.toHaveLength(0);
  });

  it("shows Host parameters and assigned Solvers from the Host API", async () => {
    renderPage();
    const panel = await screen.findByText("Read an immutable task artifact.");
    const detail = panel.closest(".ref-detail-panel") as HTMLElement;
    expect(within(detail).getByText("artifact_id")).toBeInTheDocument();
    expect(within(detail).getByText("ctf-pwn-solver")).toBeInTheDocument();
  });

  it("shows Kali capability assignment and Profile inventory from APIs", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("tab", { name: /Kali/ }));
    expect((await screen.findAllByText("kali.exec")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("ctf-pwn-solver").length).toBeGreaterThan(0);
    expect(screen.getByText("gdb 16.3")).toBeInTheDocument();
    expect(screen.getAllByText("ctf-pwn-v1").length).toBeGreaterThan(0);
  });

  it("toggles an MCP server through the management endpoint", async () => {
    const user = userEvent.setup();
    mocks.updateMCPServer.mockResolvedValue({ server: { id: "binwalk" } });
    renderPage();
    await user.click(screen.getByRole("tab", { name: /MCP Servers/ }));
    expect(await screen.findAllByText("binwalk")).not.toHaveLength(0);
    const enable = screen.getAllByRole("button").find((button) => button.textContent?.includes("启用"));
    expect(enable).toBeTruthy();
    await user.click(enable!);
    await waitFor(() => expect(mocks.updateMCPServer).toHaveBeenCalledWith("binwalk", { enabled: true }));
  });

  it("deletes an MCP server only after confirmation", async () => {
    const user = userEvent.setup();
    mocks.deleteMCPServer.mockResolvedValue({ deleted: true, server_id: "binwalk" });
    renderPage();
    await user.click(screen.getByRole("tab", { name: /MCP Servers/ }));
    await screen.findAllByText("binwalk");
    const remove = screen.getAllByRole("button").find((button) => button.textContent?.includes("删除"));
    expect(remove).toBeTruthy();
    await user.click(remove!);
    expect(mocks.deleteMCPServer).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getAllByRole("button").find((button) => button.textContent?.includes("删除"));
    await user.click(confirm!);
    await waitFor(() => expect(mocks.deleteMCPServer).toHaveBeenCalledWith("binwalk"));
  });
});
