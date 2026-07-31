import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchSkillSettings: vi.fn(),
  fetchSkillDetail: vi.fn(),
  importSkill: vi.fn(),
  updateSkill: vi.fn(),
  deleteSkill: vi.fn(),
}));

vi.mock("../api/tasks", async (original) => ({ ...await original<typeof import("../api/tasks")>(), ...mocks }));

import { SkillsPage } from "./SkillsPage";

const custom = {
  name: "custom-proof", modes: ["penetration_test"], capabilities: ["http.request"], tags: ["web"],
  version: "1", source: "custom", summary: "Custom proof workflow", editable: true, body: "# Workflow\nPreserve evidence.",
};
const builtin = {
  name: "binary-triage", modes: ["reverse_engineering", "ctf"], capabilities: ["workspace.read"], tags: ["binary"],
  version: "1", source: "builtin", summary: "Inspect binary metadata", editable: true, body: "# Workflow\nInspect metadata.",
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><SkillsPage /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchSkillSettings.mockResolvedValue({ schema_version: 3, skills: [custom, builtin] });
  mocks.fetchSkillDetail.mockImplementation((name: string) => Promise.resolve({
    skill: name === custom.name ? custom : builtin,
  }));
});

/** The skill name also appears in the detail heading, so table assertions must be scoped. */
async function findTable(container: HTMLElement): Promise<HTMLElement> {
  await waitFor(() => expect(container.querySelector(".catalog-table")).toBeTruthy());
  return container.querySelector(".catalog-table") as HTMLElement;
}

describe("SkillsPage", () => {
  it("lists real skills from the settings API", async () => {
    const { container } = renderPage();
    const table = await findTable(container);
    expect(within(table).getByText("custom-proof")).toBeInTheDocument();
    expect(within(table).getByText("binary-triage")).toBeInTheDocument();
    expect(mocks.fetchSkillSettings).toHaveBeenCalled();
  });

  it("files real tags into the reference's fixed category taxonomy", async () => {
    const { container } = renderPage();
    await findTable(container);
    const categories = container.querySelector(".skills-categories") as HTMLElement;
    expect(categories).toHaveTextContent("全部技能");
    // `binary` maps to 逆向工程; `web` maps to nothing, so it falls to 通用技能.
    expect(within(categories).getByRole("button", { name: /逆向工程/ })).toHaveTextContent("1");
    expect(within(categories).getByRole("button", { name: /通用技能/ })).toHaveTextContent("1");
    // A category with nothing filed under it stays listed but is not clickable.
    expect(within(categories).getByRole("button", { name: /报告与总结/ })).toBeDisabled();
  });

  it("filters the table by category", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await findTable(container);

    const categories = container.querySelector(".skills-categories") as HTMLElement;
    await user.click(within(categories).getByRole("button", { name: /逆向工程/ }));

    await waitFor(() => {
      const table = container.querySelector(".catalog-table") as HTMLElement;
      expect(within(table).queryByText("custom-proof")).not.toBeInTheDocument();
    });
    const table = await findTable(container);
    expect(within(table).getByText("binary-triage")).toBeInTheDocument();
  });

  it("saves an edited skill body through the real update endpoint", async () => {
    const user = userEvent.setup();
    mocks.updateSkill.mockResolvedValue({ skill: custom });
    const { container } = renderPage();
    await findTable(container);

    await user.click(await screen.findByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("tab", { name: "Instructions" }));

    const body = await screen.findByLabelText(/Instructions 正文/);
    await user.clear(body);
    await user.type(body, "# Updated");
    await user.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => expect(mocks.updateSkill).toHaveBeenCalledWith(
      "custom-proof",
      expect.objectContaining({ body: "# Updated" }),
    ));
  });

  it("deletes a custom skill only after confirmation", async () => {
    const user = userEvent.setup();
    mocks.deleteSkill.mockResolvedValue({ name: custom.name, deleted: true });
    const { container } = renderPage();
    await findTable(container);

    await user.click(await screen.findByRole("button", { name: /删除/ }));
    expect(mocks.deleteSkill).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mocks.deleteSkill).toHaveBeenCalledWith("custom-proof"));
  });

  it("blanks the fields the Skill model does not provide without labelling them", async () => {
    const { container } = renderPage();
    const table = await findTable(container);
    // 状态 / 更新时间 have no source on the Skill model — a dash, not a marker.
    expect(within(table).getAllByText("—").length).toBeGreaterThan(0);
    expect(container.querySelectorAll(".not-implemented-inline")).toHaveLength(0);
    expect(container.textContent).not.toContain("项目没有实现");
  });
});
