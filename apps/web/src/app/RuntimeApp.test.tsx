import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ fetchTasks: vi.fn(), getLLMSettings: vi.fn(), deleteTask: vi.fn() }));
vi.mock("../api/tasks", async (original) => ({ ...await original<typeof import("../api/tasks")>(), ...mocks }));
vi.mock("../pages/DashboardPage", () => ({ DashboardPage: () => <div>dashboard</div> }));
vi.mock("../pages/NewTaskPage", () => ({ NewTaskPage: () => <div>new task</div> }));
vi.mock("../pages/SessionRuntimePage", () => ({ SessionRuntimePage: () => <div>runtime</div> }));
vi.mock("../pages/SettingsPages", () => ({ CapabilitiesPage: () => null, ModelsPage: () => null, SkillsPage: () => null }));

import { RuntimeApp } from "./RuntimeApp";

describe("RuntimeApp scene task navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getLLMSettings.mockResolvedValue({ configured: true });
    mocks.fetchTasks.mockResolvedValue({ tasks: [
      ...Array.from({ length: 8 }, (_, index) => ({ task_id: `ctf_${index}`, name: `CTF task ${index}`, mode: "ctf", status: "completed", target: "", created_at: "", flags: 0, findings: 0, artifacts: 0 })),
      { task_id: "reverse_1", name: "Reverse task", mode: "reverse_engineering", status: "running", target: "", created_at: "", flags: 0, findings: 0, artifacts: 0 },
    ] });
  });

  it("groups every task by scene and independently collapses each scene", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/"]}><RuntimeApp /></MemoryRouter>);
    await waitFor(() => expect(mocks.fetchTasks).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: /新建任务/ })).toBeInTheDocument();
    const ctfGroup = screen.getByRole("button", { name: /CTF 解题.*8/ });
    const reverseGroup = screen.getByRole("button", { name: /逆向分析.*1/ });
    expect(screen.getByTitle("CTF task 7")).toBeInTheDocument();
    expect(screen.getByTitle("Reverse task")).toBeInTheDocument();
    await user.click(ctfGroup);
    expect(ctfGroup).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTitle("CTF task 7")).toBeNull();
    expect(screen.getByTitle("Reverse task")).toBeInTheDocument();
    expect(within(reverseGroup).getByText("1")).toBeInTheDocument();
  });
});
