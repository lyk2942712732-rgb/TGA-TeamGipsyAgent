import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ listTasks: vi.fn(), listAttention: vi.fn(), fetchSystemHealth: vi.fn() }));
vi.mock("../api/tga3-tasks", () => ({ listTasks: mocks.listTasks }));
vi.mock("../api/tga3-attention", () => ({ attentionApi: { list: mocks.listAttention, resume: vi.fn() } }));
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
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <RuntimeApp />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RuntimeApp TGA3 precision shell", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    mocks.listTasks.mockResolvedValue([]);
    mocks.listAttention.mockResolvedValue([]);
    mocks.fetchSystemHealth.mockResolvedValue({
      components: [{ id: "runtime", label: "TGA3", status: "healthy", detail: "ok", latencyMs: 1 }],
    });
  });

  it("renders the slim primary navigation rail, topbar telemetry, and overlays", async () => {
    renderShell("/reports");
    const navigation = screen.getByRole("navigation", { name: "主导航" });
    expect(navigation.querySelectorAll("button")).toHaveLength(4);
    expect(screen.getByText("reports")).toBeInTheDocument();
    expect(within(navigation).getByRole("button", { name: "任务" })).toBeInTheDocument();
    expect(document.querySelector(".app-topbar")).toBeInTheDocument();
    expect(await screen.findByText("空闲")).toBeInTheDocument();
    expect(await screen.findByText("健康")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审批中心，0 条待处理" })).toBeInTheDocument();
  });

  it("shows active state and a red alert dot from live data and opens the interrupt drawer", async () => {
    const user = userEvent.setup();
    mocks.listTasks.mockResolvedValue([
      { id: "running", title: "Active", scene_id: "penetration_test", state: "running", created_at: "2026-01-01", updated_at: "2026-01-01" },
    ]);
    mocks.listAttention.mockResolvedValue([
      { id: "q1", kind: "agent_failed", task_id: "running", task_title: "Active", task_state: "running", title: "Worker failed", detail: "err", created_at: "2026-01-01T00:00:00Z" },
    ]);
    renderShell("/reports");
    expect(await screen.findByRole("button", { name: /运行中/ })).toBeInTheDocument();
    const approvals = await screen.findByRole("button", { name: "审批中心，1 条待处理" });
    expect(approvals.querySelector(".topbar-notification-dot")).toBeInTheDocument();
    await user.click(approvals);
    expect(screen.getByText("Worker failed")).toBeInTheDocument();
  });

  it("keeps navigation and task breadcrumbs available in the runtime page", () => {
    renderShell("/tasks/task-1/runtime");
    expect(screen.getByText("task runtime")).toBeInTheDocument();
    expect(document.querySelector(".app-topbar")).toBeInTheDocument();
  });

  it("navigates across unified settings workspace sub-tabs", async () => {
    const user = userEvent.setup();
    renderShell("/reports");
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getByText("models")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "场景与策略" }));
    expect(screen.getByText("policies")).toBeInTheDocument();
  });

  it("rejects routes outside the TGA3 route table", () => {
    renderShell("/unknown");
    expect(screen.getByRole("heading", { name: "此入口不存在" })).toBeInTheDocument();
  });

  it("switches across all 4 visual themes and both navigation layouts with persistence", async () => {
    const user = userEvent.setup();
    renderShell("/reports");
    expect(document.documentElement.dataset.theme).toBe("tungsten");
    expect(document.documentElement.dataset.nav).toBe("rail");

    await user.click(screen.getByRole("button", { name: "图纸" }));
    expect(document.documentElement.dataset.theme).toBe("swiss");
    expect(window.localStorage.getItem("tga3.ui.theme.v1")).toBe("swiss");

    await user.click(screen.getByRole("button", { name: "画布" }));
    expect(document.documentElement.dataset.theme).toBe("spatial");
    expect(window.localStorage.getItem("tga3.ui.theme.v1")).toBe("spatial");

    await user.click(screen.getByRole("button", { name: "终端" }));
    expect(document.documentElement.dataset.theme).toBe("instrument");
    expect(window.localStorage.getItem("tga3.ui.theme.v1")).toBe("instrument");

    await user.click(screen.getByRole("button", { name: /切换导航布局/ }));
    expect(document.documentElement.dataset.nav).toBe("topbar");
    expect(window.localStorage.getItem("tga3.ui.nav.v1")).toBe("topbar");
  });

  it("opens global ⌘K command palette and navigates to tasks", async () => {
    const user = userEvent.setup();
    renderShell("/reports");
    await user.click(screen.getByRole("button", { name: "打开命令面板" }));
    const dialog = screen.getByRole("dialog", { name: "全局命令面板" });
    await user.click(within(dialog).getByRole("button", { name: /任务调度档案/ }));
    expect(screen.getByText("task list")).toBeInTheDocument();
  });
});
