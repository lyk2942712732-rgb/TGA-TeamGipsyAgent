import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({ fetchTasks: vi.fn(), getLLMSettings: vi.fn() }));
const shellMocks = vi.hoisted(() => ({ fetchDashboard: vi.fn() }));
vi.mock("../api/tasks", async (original) => ({ ...await original<typeof import("../api/tasks")>(), ...apiMocks }));
vi.mock("../api/operations-query-adapter", async (original) => ({
  ...await original<typeof import("../api/operations-query-adapter")>(),
  ...shellMocks,
}));
vi.mock("../pages/DashboardRoute", () => ({ DashboardRoute: () => <div>dashboard route</div> }));
vi.mock("../pages/ApprovalsPage", () => ({ ApprovalsPage: () => <div>global approvals</div> }));
vi.mock("../pages/NewTaskPage", () => ({ NewTaskPage: () => <div>new task</div> }));
vi.mock("../pages/TaskListPage", () => ({ TaskListPage: () => <div>task list</div> }));
vi.mock("../pages/TaskDetailPage", () => ({ TaskDetailPage: ({ taskId }: { taskId: string }) => <div>task detail {taskId}</div> }));
vi.mock("../features/runtime/TaskRuntimePage", () => ({ TaskRuntimePage: () => <div>task runtime</div> }));
vi.mock("../pages/ToolsPage", () => ({ CapabilitiesPage: () => <div>tools</div> }));
vi.mock("../pages/ModelsPage", () => ({ ModelsPage: () => <div>models</div> }));
vi.mock("../pages/SkillsPage", () => ({ SkillsPage: () => <div>skills</div> }));
vi.mock("../pages/ResourcesPage", () => ({ ResourcesPage: () => <h1>资源</h1> }));
vi.mock("../pages/ReportsPage", () => ({ ReportsPage: () => <div>reports</div> }));
vi.mock("../pages/KnowledgeBasesPage", () => ({ KnowledgeBasesPage: () => <div>knowledge bases</div> }));
vi.mock("../pages/TeamsPage", () => ({ TeamsPage: () => <div>teams</div> }));
vi.mock("../pages/SolversPage", () => ({ SolversPage: () => <div>solvers</div> }));
vi.mock("../pages/PoliciesPage", () => ({ PoliciesPage: () => <div>policies</div> }));
vi.mock("../pages/SystemPage", () => ({ SystemPage: () => <div>system</div> }));

import { RuntimeApp } from "./RuntimeApp";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function renderShell(entry: string, extra?: ReturnType<typeof LocationProbe>) {
  shellMocks.fetchDashboard.mockResolvedValue({
    schema_version: 1, generated_at: "", metrics: {}, needs_attention: [],
    active_tasks: [], recent_completed: [], system_status: [], unavailable_metrics: [],
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>
    <MemoryRouter initialEntries={[entry]}><RuntimeApp />{extra}</MemoryRouter>
  </QueryClientProvider>);
}

describe("RuntimeApp product shell", () => {
  it("renders thirteen static product entries without loading tasks or model settings", () => {
    renderShell("/resources");

    const navigation = screen.getByRole("navigation", { name: "主导航" });
    expect(navigation.querySelectorAll("button")).toHaveLength(13);
    expect(screen.getByRole("button", { name: "首页" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "任务" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tools & MCP" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "系统状态" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "资源" })).toBeInTheDocument();
    expect(apiMocks.fetchTasks).not.toHaveBeenCalled();
    expect(apiMocks.getLLMSettings).not.toHaveBeenCalled();
  });

  it("navigates through the shell without turning navigation into a data query", async () => {
    const user = userEvent.setup();
    renderShell("/resources");

    await user.click(screen.getByRole("button", { name: "审批" }));
    expect(screen.getByText("global approvals")).toBeInTheDocument();
    expect(apiMocks.fetchTasks).not.toHaveBeenCalled();
    expect(apiMocks.getLLMSettings).not.toHaveBeenCalled();
  });

  it("returns an explicit removed-route result for legacy Session URLs", () => {
    renderShell("/sessions/task%20one/replay?tab=evidence", <LocationProbe />);
    expect(screen.getByRole("heading", { name: "此入口不存在" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/sessions/task%20one/replay");
  });
});
