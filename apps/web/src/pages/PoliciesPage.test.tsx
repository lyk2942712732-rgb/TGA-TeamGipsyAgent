import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExecutionPolicyRecord } from "../api/catalog-query-adapter";

const mocks = vi.hoisted(() => ({ fetchExecutionPolicies: vi.fn() }));
vi.mock("../api/catalog-query-adapter", async (original) => ({
  ...await original<typeof import("../api/catalog-query-adapter")>(),
  ...mocks,
}));

import { PoliciesPage } from "./PoliciesPage";

function policy(mode: string, overrides: Partial<ExecutionPolicyRecord> = {}): ExecutionPolicyRecord {
  return {
    id: `${mode}-execution-policy`,
    type: "execution",
    mode,
    mode_label: mode === "ctf" ? "CTF 解题" : "渗透测试",
    preset: mode === "ctf" ? "autonomous_ctf" : "safe_observation",
    status: "available",
    source: "Task creation contract",
    editable: false,
    execution_policy: {
      preset: mode === "ctf" ? "autonomous_ctf" : "safe_observation",
      network: {
        access: mode === "ctf" ? "public_internet" : "task_sources",
        interaction: mode === "ctf" ? "interact" : "observe",
        seed_origins: [], custom_origins: [], custom_domains: [],
        custom_cidrs: [], custom_ports: [],
        deny_private_networks: true, deny_loopback: true,
        deny_link_local: true, deny_cloud_metadata: true,
        rate_limit_per_minute: 30, concurrency: 2, request_timeout_seconds: 30,
      },
      local_compute: {
        mode: "isolated", timeout_seconds: 120, concurrency: 2,
        network_inheritance: "task_network_policy",
      },
      high_impact: {
        mode: mode === "ctf" ? "approval_required" : "forbidden",
        allowed_actions: [],
      },
    },
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><PoliciesPage /></QueryClientProvider>);
}

describe("PoliciesPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders each mode's real ExecutionPolicy preset from the catalog", async () => {
    mocks.fetchExecutionPolicies.mockResolvedValue({
      items: [policy("ctf"), policy("penetration_test")],
      total: 2,
    });

    renderPage();

    expect(await screen.findAllByText("ctf-execution-policy")).toHaveLength(2);
    expect(screen.getByText("penetration_test-execution-policy")).toBeTruthy();
    // Detail panel reads the selected record's own network and compute limits.
    const detail = await waitFor(() => screen.getByLabelText(/ctf-execution-policy/));
    expect(detail.textContent).toContain("30");
    expect(detail.textContent).toContain("120");
    expect(detail.textContent).toContain("task_network_policy");
  });

  it("filters by mode and surfaces a catalog failure", async () => {
    mocks.fetchExecutionPolicies.mockResolvedValue({
      items: [policy("ctf"), policy("penetration_test")],
      total: 2,
    });
    renderPage();

    const search = await screen.findByLabelText("搜索策略名称");
    await userEvent.type(search, "penetration");

    await waitFor(() => expect(screen.queryAllByText("ctf-execution-policy")).toHaveLength(0));
    expect(screen.getAllByText("penetration_test-execution-policy").length).toBeGreaterThan(0);
  });

  it("shows an error state when the catalog cannot be read", async () => {
    mocks.fetchExecutionPolicies.mockRejectedValue(new Error("catalog offline"));

    renderPage();

    expect(await screen.findByText("catalog offline")).toBeTruthy();
  });
});
