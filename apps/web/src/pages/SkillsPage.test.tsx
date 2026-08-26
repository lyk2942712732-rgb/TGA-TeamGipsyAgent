import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ list: vi.fn(), read: vi.fn(), create: vi.fn(), save: vi.fn(), remove: vi.fn() }));
vi.mock("../api/tga3-skills", () => ({ tga3SkillsApi: mocks }));
import { SkillsPage } from "./SkillsPage";

describe("SkillsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue([{ name: "web", description: "Web testing", file_count: 2 }]);
    mocks.read.mockResolvedValue({ name: "web", description: "Web testing", file_count: 2, files: [{ path: "SKILL.md", content: "# Web\n\nInspect inputs." }, { path: "sql.md", content: "# SQL" }] });
    mocks.save.mockResolvedValue({ name: "web", description: "Updated", file_count: 2, files: [{ path: "SKILL.md", content: "# Updated" }, { path: "sql.md", content: "# SQL" }] });
    mocks.remove.mockResolvedValue(undefined);
  });

  it("reads and saves the complete Skill package through the TGA3 skill API", async () => {
    const user = userEvent.setup();
    render(<SkillsPage />);
    const editor = await screen.findByLabelText("Skill 文件内容");
    await waitFor(() => expect(editor).toHaveValue("# Web\n\nInspect inputs."));
    await user.clear(editor);
    await user.type(editor, "# Updated");
    await user.click(screen.getByRole("button", { name: "保存整个包" }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith("web", { "SKILL.md": "# Updated", "sql.md": "# SQL" }));
  });
});
