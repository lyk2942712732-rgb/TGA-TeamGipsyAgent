import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  createTask: vi.fn(async () => ({ id: "task-created" })),
  stageInput: vi.fn(async (file: File) => ({ id: `asset-${file.name}`, originalName: file.name, mimeType: file.type, mediaKind: "image", size: file.size, status: "uploaded" as const })),
  deleteStagedInput: vi.fn(),
  listScenes: vi.fn(async () => ({ schema_version: 1, scenes: [{ id: "penetration_test", name: "渗透测试", description: "Web target", system_prompt: "Test safely" }] })),
  fetchAgentModelOptions: vi.fn(async () => ({ scene_id: "penetration_test", agents: [{ id: "supervisor", display_name: "Supervisor", role: "supervisor", runtime: "openai_agents", model: { provider_id: "openai", provider_name: "OpenAI", model_id: "gpt-test", model_name: "gpt-test", protocol: "openai_responses", ready: true } }], models: [{ provider_id: "openai", provider_name: "OpenAI", model_id: "gpt-test", model_name: "gpt-test", protocol: "openai_responses", ready: true }] })),
}));
vi.mock("../api/tga3-tasks", () => mocks);
vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:image"), revokeObjectURL: vi.fn() });

import { NewTaskPage } from "./NewTaskPage";

describe("NewTaskPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads TGA3 scenes and task-level agent models", async () => {
    render(<NewTaskPage onCreated={vi.fn()} />);
    expect(await screen.findByRole("dialog", { name: "创建任务" })).toBeInTheDocument();
    expect(await screen.findByText("渗透测试")).toBeInTheDocument();
    expect(await screen.findByLabelText("Supervisor 模型")).toHaveValue("openai::gpt-test::openai_responses");
  });

  it("creates a native TGA3 task with staged files", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(<NewTaskPage onCreated={onCreated} />);
    await screen.findByText("渗透测试");
    await user.type(screen.getByLabelText("任务名称"), "Web challenge");
    await user.type(screen.getByLabelText(/描述/), "Find the flag");
    await user.upload(document.querySelector<HTMLInputElement>('input[type="file"]')!, new File(["png"], "challenge.png", { type: "image/png" }));
    await user.click(await screen.findByRole("button", { name: "创建并启动" }));
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalledWith({
      title: "Web challenge", sceneId: "penetration_test", prompt: "Find the flag",
      fileIds: ["asset-challenge.png"],
      agentModels: { supervisor: { provider_id: "openai", model_id: "gpt-test", protocol: "openai_responses" } },
    }));
    expect(onCreated).toHaveBeenCalledWith("task-created");
  });
});
