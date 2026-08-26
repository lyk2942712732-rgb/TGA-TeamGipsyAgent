import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ listTasks: vi.fn(), listAttention: vi.fn(), fetchSystemHealth: vi.fn() }));
vi.mock("../api/tga3-tasks", () => ({ listTasks: mocks.listTasks }));
vi.mock("../api/tga3-attention", () => ({ attentionApi: { list: mocks.listAttention } }));
vi.mock("../api/tga3-system", () => ({ fetchSystemHealth: mocks.fetchSystemHealth }));
vi.mock("../pages/DashboardRoute", () => ({ DashboardRoute: () => <div>dashboard route</div> }));
vi.mock("../pages/ApprovalsPage", () => ({ ApprovalsPage: () => <div>global approvals</div> }));
vi.mock("../pages/NewTaskPage", () => ({ NewTaskPage: () => <div>new task</div> }));
vi.mock("../pages/TaskListPage", () => ({ TaskListPage: () => <div>task list</div> }));
vi.mock("../features/runtime/TaskRuntimePage", () => ({ TaskRuntimePage: () => <div>task runtime</div> }));
vi.mock("../pages/ModelsPage", () => ({ ModelsPage: () => <div>models</div> }));
vi.mock("../pages/SkillsPage", () => ({ SkillsPage: () => <div>skills</div> }));
vi.mock("../pages/ReportsPage", () => ({ ReportsPage: () => <div>reports</div> }));
vi.mock("../pages/SolversPage", () => ({ SolversPage: () => <div>solvers</div> }));
vi.mock("../pages/PoliciesPage", () => ({ PoliciesPage: () => <div>policies</div> }));
vi.mock("../pages/SystemPage", () => ({ SystemPage: () => <div>system</div> }));

import { RuntimeApp } from "./RuntimeApp";

function renderShell(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><RuntimeApp /></MemoryRouter></QueryClientProvider>);
}

describe("RuntimeApp TGA3 shell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listTasks.mockResolvedValue([]);
    mocks.listAttention.mockResolvedValue([]);
    mocks.fetchSystemHealth.mockResolvedValue({ components: [{ id: "runtime", label: "TGA3", status: "healthy", detail: "ok", latencyMs: 1 }] });
  });

  it("contains only the current TGA3 navigation", async () => {
    renderShell("/reports");
    const navigation = screen.getByRole("navigation", { name: "主导航" });
    expect(navigation.querySelectorAll("button")).toHaveLength(9);
    expect(screen.getByText("reports")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "任务" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "全局搜索" })).not.toBeInTheDocument();
    expect(document.querySelector(".app-topbar")).toBeInTheDocument();
    expect(await screen.findByText("空闲")).toBeInTheDocument();
    expect(await screen.findByText("健康")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审批中心，0 条待处理" })).toBeInTheDocument();
  });

  it("shows active state and a red approval dot from live data", async () => {
    mocks.listTasks.mockResolvedValue([{ id: "running", title: "Active", scene_id: "penetration_test", state: "running", created_at: "2026-01-01", updated_at: "2026-01-01" }]);
    mocks.listAttention.mockResolvedValue([{ id: "q1" }]);
    renderShell("/reports");
    expect(await screen.findByText("运行中")).toBeInTheDocument();
    const approvals = await screen.findByRole("button", { name: "审批中心，1 条待处理" });
    expect(approvals.querySelector(".topbar-notification-dot")).toBeInTheDocument();
  });

  it("keeps the task runtime page free of the global topbar", () => {
    renderShell("/tasks/task-1/runtime");
    expect(screen.getByText("task runtime")).toBeInTheDocument();
    expect(document.querySelector(".app-topbar")).not.toBeInTheDocument();
  });

  it("navigates to a current configuration page", async () => {
    const user = userEvent.setup();
    renderShell("/reports");
    await user.click(screen.getByRole("button", { name: "场景" }));
    expect(screen.getByText("policies")).toBeInTheDocument();
  });

  it("rejects routes outside the TGA3 route table", () => {
    renderShell("/unknown");
    expect(screen.getByRole("heading", { name: "此入口不存在" })).toBeInTheDocument();
  });
});
