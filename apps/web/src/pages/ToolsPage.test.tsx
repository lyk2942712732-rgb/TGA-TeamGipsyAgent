import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requestJson: vi.fn(),
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
    mcpServers: (...args: unknown[]) => mocks.mcpServers(...args),
    updateMCPServer: (...args: unknown[]) => mocks.updateMCPServer(...args),
    deleteMCPServer: (...args: unknown[]) => mocks.deleteMCPServer(...args),
  },
}));
vi.mock("../components/mcp/MCPWizard", () => ({ MCPWizard: () => <div>mcp wizard</div> }));

import { CapabilitiesPage } from "./ToolsPage";

const capability = {
  name: "http.request",
  description: "Scoped HTTP request with redirect verification.",
  kind: "network",
  risk: "active",
  modes: ["penetration_test"],
  availability: "healthy",
  budget_key: "http",
  input_schema: { properties: { method: {}, url: {}, headers: {} } },
};

const record = {
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
    if (path.includes("/capabilities")) return Promise.resolve({ capabilities: [capability] });
    if (path.includes("/tools/health")) return Promise.resolve({ configured: true, records: [record] });
    return Promise.reject(new Error(`unexpected path ${path}`));
  });
  mocks.mcpServers.mockResolvedValue({ servers: [{ id: "binwalk", config: {} }] });
});

/** Names repeat in the detail heading, so table assertions must be scoped. */
async function findTable(container: HTMLElement): Promise<HTMLElement> {
  await waitFor(() => expect(container.querySelector(".catalog-table")).toBeTruthy());
  return container.querySelector(".catalog-table") as HTMLElement;
}

describe("Tools & MCP", () => {
  it("renders the real capability registry with risk levels", async () => {
    const { container } = renderPage();
    const table = await findTable(container);
    expect(within(table).getByText("http.request")).toBeInTheDocument();
    expect(within(table).getByText("network")).toBeInTheDocument();
  });

  it("defers the approval column to task policy instead of deriving it from risk", async () => {
    const { container } = renderPage();
    const table = await findTable(container);
    // Approval comes from the task's ExecutionPolicy.high_impact mode, so the
    // column must not restate the capability's risk level.
    expect(within(table).getAllByText("按任务策略").length).toBeGreaterThan(0);
    expect(within(table).queryByText("必须审批")).not.toBeInTheDocument();
    expect(within(table).queryByText("无需审批")).not.toBeInTheDocument();
    expect(container.textContent).not.toContain("项目没有实现");
  });

  it("shows real parameter names from the capability input schema", async () => {
    const { container } = renderPage();
    await findTable(container);
    const panel = container.querySelector(".ref-detail-panel") as HTMLElement;
    await waitFor(() => expect(within(panel).getByText("method")).toBeInTheDocument());
    expect(within(panel).getByText("url")).toBeInTheDocument();
  });

  it("lists MCP servers and toggles enablement through the real endpoint", async () => {
    const user = userEvent.setup();
    mocks.updateMCPServer.mockResolvedValue({ server: { id: "binwalk" } });
    const { container } = renderPage();

    await user.click(screen.getByRole("tab", { name: /MCP Servers/ }));
    const table = await findTable(container);
    expect(within(table).getByText("binwalk")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "启用" }));
    await waitFor(() => expect(mocks.updateMCPServer).toHaveBeenCalledWith("binwalk", { enabled: true }));
  });

  it("deletes an MCP server only after confirmation", async () => {
    const user = userEvent.setup();
    mocks.deleteMCPServer.mockResolvedValue({ deleted: true, server_id: "binwalk" });
    const { container } = renderPage();

    await user.click(screen.getByRole("tab", { name: /MCP Servers/ }));
    await findTable(container);

    await user.click(await screen.findByRole("button", { name: /删除/ }));
    expect(mocks.deleteMCPServer).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mocks.deleteMCPServer).toHaveBeenCalledWith("binwalk"));
  });
});
