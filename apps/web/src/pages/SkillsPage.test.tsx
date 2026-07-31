import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ fetchSkillSettings: vi.fn(), fetchSkillDetail: vi.fn(), importSkill: vi.fn(), updateSkill: vi.fn(), deleteSkill: vi.fn() }));
vi.mock("../api/tasks", async (original) => ({ ...await original<typeof import("../api/tasks")>(), ...mocks }));
import { SkillsPage } from "./SkillsPage";

const custom = { name: "custom-proof", modes: ["penetration_test"], capabilities: ["http.request"], tags: ["web"], version: "1", source: "custom", summary: "Custom proof workflow", editable: true, body: "# Workflow\nPreserve evidence." };
const builtin = { name: "binary-triage", modes: ["reverse_engineering", "ctf"], capabilities: ["workspace.read"], tags: ["binary"], version: "1", source: "builtin", summary: "Inspect binary metadata", editable: true, body: "# Workflow\nInspect metadata." };

describe("SkillsPage catalog layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchSkillSettings.mockResolvedValue({ schema_version: 3, skills: [custom, builtin] });
    mocks.fetchSkillDetail.mockImplementation(async (name: string) => ({ skill: name === custom.name ? custom : builtin }));
    mocks.importSkill.mockResolvedValue({ skill: custom });
    mocks.updateSkill.mockResolvedValue({ skill: { ...custom, version: "2" } });
    mocks.deleteSkill.mockResolvedValue({ name: custom.name, deleted: true });
  });

  it("renders category navigation, table, and persistent detail area", async () => {
    render(<SkillsPage />);
    expect(await screen.findByRole("heading", { name: "Skills" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Skill 分类" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Skill 表格" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "custom-proof" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryAllByRole("button", { name: /上传到/ })).toHaveLength(0);
  });

  it("selects a table row and loads its detail", async () => {
    const user = userEvent.setup(); render(<SkillsPage />);
    const table = await screen.findByRole("table", { name: "Skill 表格" });
    await user.click(within(table).getByText("binary-triage"));
    expect(await screen.findByRole("heading", { name: "binary-triage" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Instructions" }));
    expect(screen.getByText(/Inspect metadata/)).toBeInTheDocument();
  });

  it("imports one markdown file and supports real editing", async () => {
    const user = userEvent.setup(); render(<SkillsPage />); await screen.findByText("custom-proof");
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["---\nname: custom-proof\n---"], "custom.md", { type: "text/markdown" });
    fireEvent.change(fileInput, { target: { files: [file] } });
    await waitFor(() => expect(mocks.importSkill).toHaveBeenCalledWith(file));
    await user.click(screen.getByRole("button", { name: "修改" }));
    const version = screen.getByLabelText("版本"); await user.clear(version); await user.type(version, "2");
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(mocks.updateSkill).toHaveBeenCalledWith("custom-proof", expect.objectContaining({ version: "2" })));
  });

  it("marks unsupported detail tabs without fabricated data", async () => {
    const user = userEvent.setup(); render(<SkillsPage />); await screen.findByRole("heading", { name: "custom-proof" });
    await user.click(screen.getByRole("button", { name: "版本历史" }));
    expect(screen.getByText(/后端尚未提供 Skill 版本历史 数据接口/)).toBeInTheDocument();
  });
});
