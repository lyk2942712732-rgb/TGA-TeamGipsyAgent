import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ listTasks: vi.fn(), listAttention: vi.fn(), fetchSystemHealth: vi.fn() }));
vi.mock("../api/tga3-tasks", () => ({ listTasks: mocks.listTasks }));
vi.mock("../api/tga3-attention", () => ({ attentionApi: { list: mocks.listAttention } }));
vi.mock("../api/tga3-system", () => ({ fetchSystemHealth: mocks.fetchSystemHealth }));

import { DashboardRoute } from "./DashboardRoute";

function renderRoute() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><DashboardRoute /></MemoryRouter></QueryClientProvider>);
}

describe("DashboardRoute", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listTasks.mockResolvedValue([]);
    mocks.listAttention.mockResolvedValue([]);
    mocks.fetchSystemHealth.mockResolvedValue({ components: [] });
  });

  it("loads the native TGA3 data sources", async () => {
    renderRoute();
    expect(await screen.findByRole("heading", { name: "TGA3 控制台" })).toBeInTheDocument();
    expect(mocks.listTasks).toHaveBeenCalledTimes(1);
    expect(mocks.listAttention).toHaveBeenCalledTimes(1);
    expect(mocks.fetchSystemHealth).toHaveBeenCalledTimes(1);
  });

  it("shows an error when a required TGA3 source fails", async () => {
    mocks.listTasks.mockRejectedValue(new Error("offline"));
    renderRoute();
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  });
});
