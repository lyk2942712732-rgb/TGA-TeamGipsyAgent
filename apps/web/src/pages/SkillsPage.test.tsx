import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchSkillSettings: vi.fn(),
  fetchPromptSettings: vi.fn(),
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

describe("SkillsPage scene library", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchSkillSettings.mockResolvedValue({ schema_version: 3, skills: [custom, builtin] });
    mocks.fetchPromptSettings.mockResolvedValue({ schema_version: 2, prompts: [] });
    mocks.fetchSkillDetail.mockImplementation(async (name: string) => ({ skill: name === custom.name ? custom : builtin }));
    mocks.importSkill.mockResolvedValue({ skill: custom });
    mocks.updateSkill.mockResolvedValue({ skill: { ...custom, version: "2" } });
    mocks.deleteSkill.mockResolvedValue({ name: custom.name, deleted: true });
  });

  it("groups skills by scene and opens full content", async () => {
    const user = userEvent.setup();
    render(<SkillsPage />);
    expect(await screen.findByText("CTF 解题")).toBeInTheDocument();
    expect(screen.getByText("渗透测试")).toBeInTheDocument();
    expect(screen.getAllByText("binary-triage")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: /custom-proof/ }));
    expect(await screen.findByRole("dialog", { name: "custom-proof" })).toHaveTextContent("Preserve evidence");
    expect(screen.getByRole("button", { name: "修改" })).toBeInTheDocument();
  });

  it("uploads a dropped markdown skill and supports editing", async () => {
    const user = userEvent.setup();
    render(<SkillsPage />);
    await screen.findByText("custom-proof");
    expect(screen.getAllByRole("button", { name: /上传到/ })).toHaveLength(5);
    const file = new File(["---\nname: custom-proof\nmodes: [penetration_test]\n---"], "custom.md", { type: "text/markdown" });
    fireEvent.drop(screen.getByRole("button", { name: "上传到 渗透测试" }), { dataTransfer: { files: [file] } });
    await waitFor(() => expect(mocks.importSkill).toHaveBeenCalledWith(file, "penetration_test"));
    await user.click(screen.getByRole("button", { name: "修改" }));
    const version = screen.getByLabelText("版本");
    await user.clear(version); await user.type(version, "2");
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(mocks.updateSkill).toHaveBeenCalledWith("custom-proof", expect.objectContaining({ version: "2" })));
  });

  it("opens built-in skills for safe overlay editing", async () => {
    const user = userEvent.setup();
    render(<SkillsPage />);
    const cards = await screen.findAllByRole("button", { name: /binary-triage/ });
    await user.click(cards[0]);
    expect(await screen.findByRole("button", { name: "删除" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "修改" }));
    expect(screen.getByLabelText("Skill 正文")).toHaveValue("# Workflow\nInspect metadata.");
  });
});
