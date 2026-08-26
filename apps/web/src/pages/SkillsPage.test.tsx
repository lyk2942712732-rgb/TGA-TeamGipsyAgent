import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ list: vi.fn(), read: vi.fn(), create: vi.fn(), importArchive: vi.fn(), save: vi.fn(), remove: vi.fn() }));
vi.mock("../api/tga3-skills", () => ({ tga3SkillsApi: mocks }));
import { SkillsPage } from "./SkillsPage";

describe("SkillsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue([{ name: "web", description: "Web testing", file_count: 2 }]);
    mocks.read.mockResolvedValue({ name: "web", description: "Web testing", file_count: 2, files: [{ path: "SKILL.md", content: "# Web\n\nInspect inputs." }, { path: "sql.md", content: "# SQL" }] });
    mocks.create.mockResolvedValue({ name: "new-skill", description: "new-skill", files: [{ path: "SKILL.md", content: "# new-skill" }] });
    mocks.importArchive.mockResolvedValue({ name: "ctf-crypto", description: "Crypto", files: [{ path: "SKILL.md", content: "# Crypto" }, { path: "rsa.md", content: "# RSA" }] });
    mocks.save.mockImplementation(async (name: string, files: Record<string, string>) => ({ name, description: "Updated", file_count: Object.keys(files).length, files: Object.entries(files).map(([path, content]) => ({ path, content })) }));
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

  it("creates a package with a custom directory name", async () => {
    const user = userEvent.setup();
    render(<SkillsPage />);
    await user.click(await screen.findByRole("button", { name: "添加 Skill 包" }));
    const dialog = screen.getByRole("dialog", { name: "添加 Skill 包" });
    await user.type(within(dialog).getByLabelText("目录名称"), "new-skill");
    await user.click(within(dialog).getByRole("button", { name: "创建空包" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith("new-skill", expect.objectContaining({ "SKILL.md": expect.stringContaining("# new-skill") })));
  });

  it("uploads, renames and deletes individual Markdown files without asking for a package path", async () => {
    const user = userEvent.setup();
    render(<SkillsPage />);
    await screen.findByLabelText("Skill 文件内容");
    expect(screen.queryByText("包内路径")).not.toBeInTheDocument();

    await user.upload(screen.getByLabelText("上传 Markdown 文件"), new File(["# Auth"], "auth.md", { type: "text/markdown" }));
    expect(await screen.findByRole("tab", { name: "auth.md" })).toBeInTheDocument();
    const filename = screen.getByLabelText("文件名");
    await user.clear(filename); await user.type(filename, "auth-checks.md");
    await user.click(screen.getByRole("button", { name: "应用文件名" }));
    expect(screen.getByRole("tab", { name: "auth-checks.md" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "sql.md" }));
    await user.click(screen.getByRole("button", { name: "删除文件" }));
    expect(screen.queryByRole("tab", { name: "sql.md" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存整个包" }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith("web", { "SKILL.md": "# Web\n\nInspect inputs.", "auth-checks.md": "# Auth" }));
  });

  it("imports a ZIP package dropped directly onto the page", async () => {
    const { container } = render(<SkillsPage />);
    await screen.findByLabelText("Skill 文件内容");
    const archive = new File(["zip"], "ctf-crypto.zip", { type: "application/zip" });
    fireEvent.drop(container.querySelector(".skills-page")!, { dataTransfer: { files: [archive], types: ["Files"] } });
    await waitFor(() => expect(mocks.importArchive).toHaveBeenCalledWith(archive));
  });
});
