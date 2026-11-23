import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import sceneConfig from "../../../../tga3/config/scenes.json";

const mocks = vi.hoisted(() => ({
  createTask: vi.fn(async () => ({ id: "task-created" })),
  stageInput: vi.fn(async (file: File) => ({ id: `asset-${file.name}`, originalName: file.name, mimeType: file.type, mediaKind: "image", size: file.size, status: "uploaded" as const })),
  deleteStagedInput: vi.fn(),
  fetchKnowledgeSources: vi.fn(async () => ({ online: { available: true }, local: { available: true, documents: 773 } })),
  fetchTaskCloneConfig: vi.fn(),
  listScenes: vi.fn(async () => ({ schema_version: 1, scenes: [{ id: "penetration_test", name: "渗透测试", description: "Web target", system_prompt: "Test safely" }] })),
  fetchAgentModelOptions: vi.fn(async () => ({ scene_id: "penetration_test", agents: [{ id: "worker-openai", display_name: "OpenAI Worker", role: "worker", runtime: "codex_cli", model: { provider_id: "openai", provider_name: "OpenAI", model_id: "gpt-test", model_name: "gpt-test", protocol: "openai_responses", ready: true } }], models: [{ provider_id: "openai", provider_name: "OpenAI", model_id: "gpt-test", model_name: "gpt-test", protocol: "openai_responses", ready: true }] })),
}));
vi.mock("../api/tga3-tasks", () => mocks);
vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:image"), revokeObjectURL: vi.fn() });

import { NewTaskPage } from "./NewTaskPage";

describe("NewTaskPage", () => {
  beforeEach(() => { window.localStorage.clear(); vi.clearAllMocks(); mocks.listScenes.mockResolvedValue(sceneConfig); mocks.fetchKnowledgeSources.mockResolvedValue({ online: { available: true }, local: { available: true, documents: 773 } }); });

  it("loads TGA3 scenes and task-level agent models", async () => {
    render(<NewTaskPage onCreated={vi.fn()} />);
    expect(await screen.findByRole("region", { name: "创建任务" })).toBeInTheDocument();
    await screen.findByText("Web渗透测试");
    expect(within(screen.getByRole("group", { name: "题目类型：场景" })).getAllByRole("button")
      .map((button) => button.querySelector("strong")?.textContent)).toEqual([
        "Web渗透测试", "Pwn", "Re逆向", "Misc安全杂项", "Crypto密码学问题", "AI安全", "AOE多题",
      ]);
    expect(await screen.findByLabelText("OpenAI Worker 模型")).toHaveValue(JSON.stringify(["openai", "gpt-test"]));
  });

  it("creates a native TGA3 task with staged files", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(<NewTaskPage onCreated={onCreated} />);
    await user.click(await screen.findByRole("button", { name: /AI安全/ }));
    await waitFor(() => expect(mocks.fetchAgentModelOptions).toHaveBeenLastCalledWith("ai_security"));
    await user.type(screen.getByLabelText("任务名称"), "Web challenge");
    await user.type(screen.getByLabelText(/描述/), "Find the flag");
    await user.upload(document.querySelector<HTMLInputElement>('input[type="file"]')!, new File(["png"], "challenge.png", { type: "image/png" }));
    await user.click(await screen.findByRole("button", { name: "创建并启动" }));
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalledWith({
      title: "Web challenge", sceneId: "ai_security", prompt: "Find the flag", knowledgeSource: "online", produceReport: true,
      fileIds: ["asset-challenge.png"],
      agentModels: { "worker-openai": { provider_id: "openai", model_id: "gpt-test", protocol: "openai_responses" } },
    }));
    expect(onCreated).toHaveBeenCalledWith("task-created");
  });
  it("shows AOE settings only for AOE and sends the selected submission mode", async () => {
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await screen.findByText("Web渗透测试");
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "自动提交 Flag" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /AOE多题/ }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Worker 知识库" }), "local");
    expect(screen.getByText(/本地已索引 773 篇文档/)).toBeInTheDocument();
    const autoSubmit = screen.getByRole("checkbox", { name: "自动提交 Flag" });
    expect(autoSubmit).toBeChecked();
    await user.click(autoSubmit);
    expect(screen.queryByRole("group", { name: "执行与产出" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Worker 知识库" })).toHaveValue("local");
    const report = screen.getByRole("checkbox", { name: "产出报告" });
    expect(autoSubmit.closest(".task-run-options")).toContainElement(report);
    expect(report).toBeChecked();
    await user.click(report);
    const first = screen.getByRole("spinbutton", { name: /第一次尝试/ });
    const second = screen.getByRole("spinbutton", { name: /第二次尝试/ });
    const joint = screen.getByRole("spinbutton", { name: /共同攻坚/ });
    expect(first).toHaveValue(30); expect(second).toHaveValue(15); expect(joint).toHaveValue(20);
    await user.clear(first); await user.type(first, "5");
    await user.clear(second); await user.type(second, "10");
    await user.clear(joint); await user.type(joint, "25");
    await user.type(screen.getByLabelText("任务名称"), "Batch");
    await user.type(screen.getByLabelText(/描述/), "Enumerate and solve the supplied questions");
    await user.click(screen.getByRole("button", { name: "创建并启动" }));
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalledWith(expect.objectContaining({ sceneId: "aoe", knowledgeSource: "local",
      aoeFirstAttemptMinutes: 5, aoeSecondAttemptMinutes: 10, aoeJointMinutes: 25, aoeAutoSubmitFlag: false, produceReport: false })));
  });
  it("disables local selection until an index is available", async () => {
    mocks.fetchKnowledgeSources.mockResolvedValueOnce({ online: { available: true }, local: { available: false, documents: 0 } });
    render(<NewTaskPage onCreated={vi.fn()} />);
    expect(await screen.findByRole("option", { name: /本地知识库.*未就绪/ })).toBeDisabled();
    expect(screen.getByText(/请先运行 tga3 sync-knowledge/)).toBeInTheDocument();
  });

  it("restores unsubmitted content from banner and falls back unavailable models on one-click fill", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      "tga3.newTask.unsubmitted.v1",
      JSON.stringify({
        name: "Half Written Task",
        description: "Unsaved notes",
        sceneId: "penetration_test",
        aoeMinutes: { first: "30", second: "15", joint: "20" },
        produceReport: true,
        aoeAutoSubmitFlag: true,
        knowledgeSource: "online",
        agentModels: {},
        savedAt: "2026-11-23T10:00:00Z",
      }),
    );
    window.localStorage.setItem(
      "tga3.newTask.lastSubmitted.v1",
      JSON.stringify({
        name: "Last AOE Event",
        description: "https://ctf.example token=abc",
        sceneId: "penetration_test",
        aoeMinutes: { first: "25", second: "12", joint: "18" },
        produceReport: false,
        aoeAutoSubmitFlag: true,
        knowledgeSource: "online",
        agentModels: {
          "worker-openai": { provider_id: "openai", model_id: "deleted-old-model", protocol: "openai_responses" },
        },
        savedAt: "2026-11-23T09:00:00Z",
      }),
    );

    render(<NewTaskPage onCreated={vi.fn()} />);
    await screen.findByText("Web渗透测试");

    // Restore unsubmitted banner works cleanly.
    expect(screen.getByText(/检测到上次未提交的内容/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "点击恢复" }));
    expect(screen.getByLabelText("任务名称")).toHaveValue("Half Written Task");
    expect(screen.getByLabelText(/描述/)).toHaveValue("Unsaved notes");

    // One-click fill last config falls back deleted model to default and shows light notice.
    await user.click(screen.getByRole("button", { name: "一键填充上次配置" }));
    expect(screen.getByLabelText("任务名称")).toHaveValue("Last AOE Event");
    expect(screen.getByLabelText(/描述/)).toHaveValue("https://ctf.example token=abc");
    expect(screen.getByLabelText("OpenAI Worker 模型")).toHaveValue(JSON.stringify(["openai", "gpt-test"]));
    expect(screen.getByText(/已自动回退为当前默认模型：OpenAI Worker/)).toBeInTheDocument();
  });

  it("shows hover memory popover for remembered fields and supports 1-click fill + undo", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      "tga3.newTask.fieldHistory.v1",
      JSON.stringify({
        title: [{ value: "GeekCTF 2026", savedAt: "2026-11-23T09:30:00Z" }],
        "description:penetration_test": [{ value: "Cookie: session=xyz", savedAt: "2026-11-23T09:30:00Z" }],
      }),
    );

    render(<NewTaskPage onCreated={vi.fn()} />);
    await screen.findByText("Web渗透测试");

    const titleInput = screen.getByLabelText("任务名称");
    await user.type(titleInput, "Temp");
    await user.hover(titleInput);
    const popover = await screen.findByRole("region", { name: "任务名称 历史记忆" });
    await user.click(within(popover).getByRole("button", { name: /GeekCTF 2026/ }));
    expect(titleInput).toHaveValue("GeekCTF 2026");

    // Undo restores previous user input.
    await user.click(screen.getByRole("button", { name: "撤销填入" }));
    expect(titleInput).toHaveValue("Temp");
  });
});
