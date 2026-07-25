import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ fetchAgentPromptSettings: vi.fn(), updateAgentPromptSettings: vi.fn() }));
vi.mock("../api/tasks", async (original) => ({ ...await original<typeof import("../api/tasks")>(), ...mocks }));

import { SystemPromptPage } from "./SystemPromptPage";

const prompts = {
  schema_version: 1 as const,
  common_system_prompt: "Use evidence and controlled tools.",
  modes: ["ctf", "penetration_test", "incident_response", "vulnerability_research", "reverse_engineering"].map((id) => ({
    id, label: id === "ctf" ? "CTF 解题" : id, methodology: ["collect evidence"], completion_focus: "Complete with evidence.", observer_focus: "Watch evidence quality.",
  })),
};

describe("SystemPromptPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchAgentPromptSettings.mockResolvedValue(prompts);
    mocks.updateAgentPromptSettings.mockImplementation(async (value) => value);
  });

  it("edits and saves prompts independently from Skills", async () => {
    const user = userEvent.setup();
    render(<SystemPromptPage />);
    expect(await screen.findByRole("heading", { name: "System Prompt" })).toBeInTheDocument();
    const common = screen.getByLabelText("通用系统约束");
    await user.clear(common); await user.type(common, "Custom common prompt");
    fireEvent.change(screen.getByLabelText("CTF 解题 分析方法"), { target: { value: "step one\nstep two" } });
    await user.click(screen.getByRole("button", { name: "保存 System Prompt" }));
    await waitFor(() => expect(mocks.updateAgentPromptSettings).toHaveBeenCalledWith(expect.objectContaining({
      common_system_prompt: "Custom common prompt",
      modes: expect.arrayContaining([expect.objectContaining({ id: "ctf", methodology: ["step one", "step two"] })]),
    })));
  });
});
