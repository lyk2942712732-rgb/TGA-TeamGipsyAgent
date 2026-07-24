import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  createTask: vi.fn(async () => ({ task_id: "task_created", status: "created", scheduled: false, mcp_capabilities: { server_ids: ["binwalk"], tools: [] } })),
  stageInput: vi.fn(async (file: File) => ({ id: `asset_${(file.type.startsWith("image/") ? "b" : "a").repeat(32)}`, originalName: file.name, mimeType: file.type || "text/plain", mediaKind: file.type.startsWith("image/") ? "image" : "text", size: file.size, sha256: "b".repeat(64), status: "uploaded" as const })),
  deleteStagedInput: vi.fn(async () => ({ asset_id: `asset_${"a".repeat(32)}`, deleted: true })),
  fetchModeProfiles: vi.fn(() => new Promise(() => undefined)),
}));

const backendPolicy = {
  preset: "autonomous_ctf" as const,
  network: { access: "public_internet" as const, interaction: "interact" as const, seed_origins: [], custom_origins: [], custom_domains: [], custom_cidrs: [], deny_private_networks: true, deny_loopback: true, deny_link_local: true, deny_cloud_metadata: true, rate_limit_per_minute: 42, concurrency: 3, request_timeout_seconds: 25 },
  local_compute: { mode: "isolated" as const, timeout_seconds: 90, concurrency: 2, network_inheritance: "task_network_policy" as const },
  high_impact: { mode: "approval_required" as const, allowed_actions: [] },
};

vi.mock("../api/tasks", async (importOriginal) => ({ ...await importOriginal<typeof import("../api/tasks")>(), ...mocks }));
vi.mock("../runtime/api-v2", () => ({ runtimeApi: { toolHealth: vi.fn(async () => ({ healthy: true, records: [{ server: "binwalk", configured: true, enabled: true, discovered: true }, { server: "disabled", configured: true, enabled: false, discovered: true }] })) } }));
vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:preview"), revokeObjectURL: vi.fn() });

import { NewTaskPage } from "./NewTaskPage";

describe("NewTaskPage multimodal input flow", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows one prompt composer for text and attachments in step three", async () => {
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    expect(screen.getByText(/多模态输入/)).toBeInTheDocument();
    expect(screen.getByLabelText("任务提示词")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择文件" })).toBeInTheDocument();
    expect(screen.queryByText("Hint 附件")).toBeNull();
    expect(screen.queryByLabelText("目标 URL")).toBeNull();
    expect(screen.queryByText("代码仓库")).toBeNull();
    expect(screen.queryByText(/MCP Resource|MCP Tool/)).toBeNull();
  });

  it("keeps step four limited to execution boundaries", async () => {
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /执行边界/ }));
    expect(screen.getByLabelText("网络访问范围")).toBeInTheDocument();
    expect(screen.getByLabelText("本地计算")).toBeInTheDocument();
    expect(screen.getByLabelText("高影响动作")).toBeInTheDocument();
    expect(screen.queryByText(/MCP 服务与方法授权/)).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /MCP/ })).toBeNull();
  });

  it("uploads multiple files, renders an image thumbnail, and removes staged assets", async () => {
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    const inputs = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
    const text = new File(["hello"], "challenge.txt", { type: "text/plain" });
    const image = new File(["png"], "topology.png", { type: "image/png" });
    await user.upload(inputs[0], [text, image]);
    await waitFor(() => expect(mocks.stageInput).toHaveBeenCalledTimes(2));
    expect(await screen.findByAltText("topology.png 缩略图")).toHaveAttribute("src", "blob:preview");
    await user.click(screen.getByRole("button", { name: "删除 challenge.txt" }));
    await waitFor(() => expect(mocks.deleteStagedInput).toHaveBeenCalled());
  });

  it("shows useful upload errors and retains failed file state", async () => {
    mocks.stageInput.mockRejectedValueOnce(new Error("File exceeds the 32 MB limit"));
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    await user.upload(input, new File(["x"], "large.bin"));
    expect(await screen.findByRole("alert")).toHaveTextContent("large.bin: File exceeds the 32 MB limit");
    expect(screen.getByText("失败")).toBeInTheDocument();
  });

  it("summarizes only globally available MCP services and submits asset ids", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(<NewTaskPage onCreated={onCreated} />);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    await user.upload(input, new File(["task"], "task.txt", { type: "text/plain" }));
    await screen.findByText("已上传");
    await user.type(screen.getByLabelText("任务提示词"), "Analyze carefully");
    await user.click(screen.getByRole("button", { name: /创建摘要/ }));
    expect(await screen.findByText("binwalk")).toBeInTheDocument();
    expect(screen.queryByText("disabled")).toBeNull();
    await user.click(screen.getByRole("button", { name: "创建任务并开始" }));
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalledWith(expect.objectContaining({
      input: { text: "Analyze carefully", fileIds: [`asset_${"a".repeat(32)}`] },
    })));
    const submitted = mocks.createTask.mock.calls[0][0] as Record<string, unknown>;
    expect(submitted).not.toHaveProperty("mcp_servers");
    expect(submitted).not.toHaveProperty("targets");
    expect(onCreated).toHaveBeenCalledWith("task_created");
  });

  it("allows a prompt without requiring an attachment", async () => {
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    await user.type(screen.getByLabelText("任务提示词"), "Review the supplied target and explain the first verification step.");
    await user.click(screen.getByRole("button", { name: /创建摘要/ }));
    await user.click(screen.getByRole("button", { name: "创建任务并开始" }));
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalledWith(expect.objectContaining({
      input: { text: "Review the supplied target and explain the first verification step.", fileIds: [] },
    })));
  });

  it("reset clears uploaded state and staging", async () => {
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    await user.upload(document.querySelector<HTMLInputElement>('input[type="file"]')!, new File(["x"], "old.txt"));
    await screen.findByText("old.txt");
    await user.click(screen.getByRole("button", { name: "重置" }));
    expect(screen.queryByText("old.txt")).toBeNull();
    expect(mocks.deleteStagedInput).toHaveBeenCalled();
  });

  it("uses backend policy defaults without overwriting a mode selected before profiles resolve", async () => {
    let resolveProfiles!: (value: unknown) => void;
    mocks.fetchModeProfiles.mockImplementationOnce(() => new Promise((resolve) => { resolveProfiles = resolve; }));
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /渗透测试/ }));
    resolveProfiles({ schema_version: 5, profiles: [{ id: "ctf", label: "CTF 解题", description: "CTF", default_goal: "backend goal", default_mode_config: { mode: "ctf", subtype: "web" }, default_execution_policy: backendPolicy, allowed_input_kinds: [], required_conditions: [], recommended_capabilities: [], prompt_instruction: "", completion_validator: "ctf", report_sections: [], uses_flag: true, advanced_settings: [], mode_config_schema: {}, execution_policy_schema: {} }] });
    await user.click(screen.getByRole("button", { name: /执行边界/ }));
    expect(screen.getByLabelText("执行策略")).toHaveValue("safe_observation");
  });

  it("marks edited policy details as custom", async () => {
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /执行边界/ }));
    await user.selectOptions(screen.getByLabelText("网络访问范围"), "disabled");
    expect(screen.getByLabelText("执行策略")).toHaveValue("custom");
  });

  it("allows an explicit custom CIDR rule", async () => {
    const user = userEvent.setup();
    render(<NewTaskPage onCreated={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /执行边界/ }));
    await user.selectOptions(screen.getByLabelText("网络访问范围"), "custom");
    await user.type(screen.getByLabelText("自定义 CIDR"), "198.18.0.0/15");
    expect(screen.getByLabelText("执行策略")).toHaveValue("custom");
    expect(screen.getByLabelText("自定义 CIDR")).toHaveValue("198.18.0.0/15");
  });
});
