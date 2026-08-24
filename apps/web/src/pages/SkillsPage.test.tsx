import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ list: vi.fn(), read: vi.fn(), save: vi.fn(), remove: vi.fn() }));
vi.mock("../api/tga3-skills", () => ({ tga3SkillsApi: mocks }));
import { SkillsPage } from "./SkillsPage";

describe("SkillsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue([{ name: "web", description: "Web testing" }]);
    mocks.read.mockResolvedValue({ name: "web", content: "# Web\n\nInspect inputs." });
    mocks.save.mockResolvedValue({ name: "web", content: "# Web" });
    mocks.remove.mockResolvedValue(undefined);
  });

  it("reads and saves SKILL.md through the TGA3 skill API", async () => {
    const user = userEvent.setup();
    render(<SkillsPage />);
    const editor = await screen.findByLabelText("Skill 内容");
    expect(editor).toHaveValue("# Web\n\nInspect inputs.");
    await user.clear(editor);
    await user.type(editor, "# Updated");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith("web", "# Updated"));
  });
});
