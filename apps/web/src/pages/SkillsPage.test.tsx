import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchSkillSettings: vi.fn(), fetchSkillDetail: vi.fn(), fetchSkillDocument: vi.fn(),
  createSkill: vi.fn(), importSkill: vi.fn(), updateSkill: vi.fn(), putSkillDocument: vi.fn(),
  deleteSkillDocument: vi.fn(), deleteSkill: vi.fn(),
}));
vi.mock("../api/tasks", async (original) => ({ ...await original<typeof import("../api/tasks")>(), ...mocks }));
import { SkillsPage } from "./SkillsPage";

const crypto = {
  name: "ctf-crypto", tags: ["ctf", "crypto"], version: "2", summary: "Crypto methods", entrypoint: "SKILL.md" as const,
  file_count: 2, total_bytes: 120, content_sha256: "a".repeat(64), enabled: true,
  instructions: "# Crypto\nRoute by topic.",
  documents: [
    { path: "SKILL.md", title: "Crypto", size: 60, sha256: "b".repeat(64) },
    { path: "rsa.md", title: "RSA", size: 60, sha256: "c".repeat(64) },
  ],
};
const web = { ...crypto, name: "web-recon", version: "1", summary: "Web recon", tags: ["web"], file_count: 1, documents: [crypto.documents[0]] };

function renderPage() { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); return render(<QueryClientProvider client={client}><SkillsPage /></QueryClientProvider>); }

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchSkillSettings.mockResolvedValue({ schema_version: 2, root: "runs2/.config/skills", skills: [crypto, web] });
  mocks.fetchSkillDetail.mockImplementation((name: string) => Promise.resolve({ skill: name === crypto.name ? crypto : web }));
  mocks.fetchSkillDocument.mockResolvedValue({ document: { ...crypto.documents[1], content: "# RSA\nCheck key material." } });
});

describe("SkillsPage", () => {
  it("lists directory packages from the single config source", async () => {
    const { container } = renderPage();
    await screen.findByText("ctf-crypto");
    expect(screen.getByText("runs2/.config/skills")).toBeInTheDocument();
    expect(container.querySelector(".skill-package-list")).toHaveTextContent("web-recon");
    const detail = await screen.findByLabelText("ctf-crypto 详情");
    expect(detail).toHaveTextContent("2 份 / 120 B");
  });

  it("creates a package with SKILL.md instructions", async () => {
    const user = userEvent.setup(); mocks.createSkill.mockResolvedValue({ skill: crypto }); renderPage();
    await user.click(await screen.findByRole("button", { name: /新建 Skill 包/ }));
    await user.type(screen.getByLabelText("Skill 包名"), "ctf-crypto");
    await user.type(screen.getByLabelText("Skill 简介"), "Crypto methods");
    await user.click(screen.getByRole("button", { name: "创建包" }));
    await waitFor(() => expect(mocks.createSkill).toHaveBeenCalledWith(expect.objectContaining({ name: "ctf-crypto", description: "Crypto methods" })));
  });

  it("edits the package entrypoint", async () => {
    const user = userEvent.setup(); mocks.updateSkill.mockResolvedValue({ skill: crypto }); renderPage();
    const instructions = await screen.findByLabelText("编辑 Skill Instructions");
    await user.clear(instructions); await user.type(instructions, "# Updated");
    await user.click(screen.getByRole("button", { name: /保存 SKILL.md/ }));
    await waitFor(() => expect(mocks.updateSkill).toHaveBeenCalledWith("ctf-crypto", expect.objectContaining({ instructions: "# Updated" })));
  });

  it("opens a package document on demand", async () => {
    const user = userEvent.setup(); renderPage();
    await user.click(await screen.findByRole("button", { name: /RSA/ }));
    await waitFor(() => expect(document.querySelector(".skill-document-view pre")).toHaveTextContent("# RSA Check key material."));
    expect(mocks.fetchSkillDocument).toHaveBeenCalledWith("ctf-crypto", "rsa.md");
  });

  it("deletes a whole package only after confirmation", async () => {
    const user = userEvent.setup(); mocks.deleteSkill.mockResolvedValue({ name: "ctf-crypto", deleted: true }); renderPage();
    await user.click(await screen.findByRole("button", { name: "删除包" }));
    expect(mocks.deleteSkill).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "删除整个包" }));
    await waitFor(() => expect(mocks.deleteSkill).toHaveBeenCalledWith("ctf-crypto"));
  });
});
