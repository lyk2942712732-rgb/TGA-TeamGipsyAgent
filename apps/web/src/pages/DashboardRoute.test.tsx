import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ fetchDashboard: vi.fn() }));
vi.mock("../api/operations-query-adapter", async (original) => ({
  ...await original<typeof import("../api/operations-query-adapter")>(),
  ...mocks,
}));
vi.mock("./DashboardPage", () => ({
  DashboardPage: ({ value }: { value: { metrics: { running_tasks: number } } }) => <div>dashboard {value.metrics.running_tasks}</div>,
}));

import { DashboardRoute } from "./DashboardRoute";

const response = {
  schema_version: 1,
  generated_at: "2026-07-30T00:00:00Z",
  metrics: { running_tasks: 3, pending_approvals: 2, awaiting_user_input: 1, blocked_tasks: 1, active_solvers: 4 },
  needs_attention: [],
  active_tasks: [],
  recent_completed: [],
  system_status: [],
  unavailable_metrics: [],
};

function renderRoute() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><DashboardRoute /></MemoryRouter></QueryClientProvider>);
}

describe("DashboardRoute data ownership", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchDashboard.mockResolvedValue(response);
  });

  it("loads exactly one operational aggregate when the route mounts", async () => {
    renderRoute();
    expect(screen.getByLabelText("正在读取运营摘要")).toBeInTheDocument();
    expect(await screen.findByText("dashboard 3")).toBeInTheDocument();
    expect(mocks.fetchDashboard).toHaveBeenCalledTimes(1);
  });

  it("renders an honest error state when aggregation fails", async () => {
    mocks.fetchDashboard.mockRejectedValue(new Error("offline"));
    renderRoute();
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  });
});
