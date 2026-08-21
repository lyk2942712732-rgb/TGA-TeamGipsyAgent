import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  createTask: vi.fn(async () => ({ task_id: "task-created", status: "running", scheduled: true })),
  stageInput: vi.fn(async (file: File) => ({ id: `asset-${file.name}`, originalName: file.name, mimeType: file.type, mediaKind: file.type.startsWith("image/") ? "image" : "other", size: file.size, sha256: "pending", status: "uploaded" as const })),
  deleteStagedInput: vi.fn(async () => ({ deleted: true })),
  fetchAgentModelOptions: vi.fn(async (mode: string) => ({ mode, agents: [{ id: "supervisor", display_name: "Supervisor", role: "supervisor", runtime: "openai_agents", model: { provider_id: "openai", provider_name: "OpenAI", model_id: "gpt-test", model_name: "gpt-test", protocol: "openai_responses", ready: true } }], models: [{ provider_id: "openai", provider_name: "OpenAI", protocol: "openai_responses", model_id: "gpt-test", model_name: "gpt-test", ready: true }] })),
  fetchModeProfiles: vi.fn(async () => ({ schema_version: 1, profiles: [
    ["penetration_test", "渗透测试"], ["incident_response", "应急响应"], ["vulnerability_research", "漏洞挖掘"], ["reverse_engineering", "逆向分析"],
    ["pwn", "Pwn"], ["security_misc", "安全杂项"], ["cryptography", "密码"], ["forensics", "取证"],
  ].map(([id, label]) => ({ id, label, description: `${label}说明`, default_goal: "", default_mode_config: { mode: id }, default_execution_policy: {}, fields: [], allowed_input_kinds: [], required_conditions: [], recommended_capabilities: [], completion_validator: "final_candidate", report_sections: [], uses_flag: true })) })),
}));

vi.mock("../api/tasks", async (original) => ({ ...await original<typeof import("../api/tasks")>(), ...mocks }));
vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:image"), revokeObjectURL: vi.fn() });

import { NewTaskPage } from "./NewTaskPage";

describe("NewTaskPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("opens as a single modal with eight fixed scenes and task-level Solver model selectors", async () => {
    render(<NewTaskPage onCreated={vi.fn()} />);
    expect(await screen.findByRole("dialog", { name: "创建任务" })).toBeInTheDocument();
    expect(screen.getByText("题目类型：场景")).toBeInTheDocument();
    expect(screen.getAllByRole("button").filter((button) => ["渗透测试", "应急响应", "漏洞挖掘", "逆向分析", "Pwn", "安全杂项", "密码", "取证"].some((name) => button.textContent?.includes(name)))).toHaveLength(8);
    expect(screen.queryByText("解题架构")).toBeNull();
    expect(screen.queryByText("调度模型")).toBeNull();
    expect(screen.queryByText("工作模型")).toBeNull();
    expect(await screen.findByLabelText("Supervisor 模型")).toHaveValue("openai::gpt-test::openai_responses");
  });

  it("submits the description as initial prompt and includes multimodal files", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(<NewTaskPage onCreated={onCreated} />);
    await screen.findByText("渗透测试说明");
    await user.type(screen.getByLabelText("任务名称"), "Web challenge");
    await user.type(screen.getByLabelText(/描述/), "分析目标并拿到 flag");
    await user.upload(document.querySelector<HTMLInputElement>('input[type="file"]')!, new File(["png"], "challenge.png", { type: "image/png" }));
    await screen.findByText("challenge.png");
    await user.click(screen.getByRole("button", { name: "创建并启动" }));
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalledWith(expect.objectContaining({
      name: "Web challenge", mode: "penetration_test", goal: "分析目标并拿到 flag", input: { text: "", fileIds: ["asset-challenge.png"] }, agentModels: { supervisor: { provider_id: "openai", model_id: "gpt-test", protocol: "openai_responses" } },
    })));
    expect(onCreated).toHaveBeenCalledWith("task-created");
  });
});
