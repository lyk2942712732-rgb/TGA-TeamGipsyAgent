import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  createTask: vi.fn(async () => ({ task_id: "task_created", status: "created", scheduled: false, mcp_capabilities: { server_ids: ["binwalk"], tools: [] } })),
  preflightTask: vi.fn(async () => ({
    fingerprint: "f".repeat(64), task_id: "task_draft",
    checks: [{ id: "model", status: "passed", detail: "verified model snapshot" }],
    skill_catalog: { strategy: "worker_on_demand", package_count: 1, content_sha256: "a".repeat(64) },
    mcp_catalog_version: "catalog-test", model_verification_id: "verify-test",
  })),
  stageInput: vi.fn(async (file: File) => ({ id: `asset_${(file.type.startsWith("image/") ? "b" : "a").repeat(32)}`, originalName: file.name, mimeType: file.type || "text/plain", mediaKind: file.type.startsWith("image/") ? "image" : "text", size: file.size, sha256: "b".repeat(64), status: "uploaded" as const })),
  deleteStagedInput: vi.fn(async () => ({ asset_id: `asset_${"a".repeat(32)}`, deleted: true })),
  fetchModeProfiles: vi.fn(),
  fetchSkillSettings: vi.fn(async () => ({ schema_version: 2, root: "runs2/.config/skills", skills: [
    { name: "web-recon", tags: ["web"], version: "1", summary: "Map web endpoints", entrypoint: "SKILL.md", file_count: 2, total_bytes: 100, content_sha256: "a".repeat(64), enabled: true },
    { name: "binary-triage", tags: ["binary"], version: "1", summary: "Inspect binary metadata", entrypoint: "SKILL.md", file_count: 1, total_bytes: 50, content_sha256: "b".repeat(64), enabled: true },
  ] })),
  fetchAgentModelOptions: vi.fn(async (mode: string) => ({
    mode,
    agents: [{ id: "supervisor", role: "supervisor", specialties: ["planning"], required: true, model: { provider_id: "provider_test", provider_name: "Test Provider", model_id: "model_test", model_name: "test-model", verification_status: "verified", ready: true } }],
    models: [{
      provider_id: "provider_test", provider_name: "Test Provider",
      model_id: "model_test", model_name: "test-model", api_key_id: "key_test",
      verification_status: "verified", ready: true,
    }],
  })),
}));

const backendPolicy = {
  preset: "autonomous_ctf" as const,
  network: { access: "public_internet" as const, interaction: "interact" as const, seed_origins: [], custom_origins: [], custom_domains: [], custom_cidrs: [], deny_private_networks: true, deny_loopback: true, deny_link_local: true, deny_cloud_metadata: true, rate_limit_per_minute: 42, concurrency: 3, request_timeout_seconds: 25 },
  local_compute: { mode: "isolated" as const, timeout_seconds: 90, concurrency: 2, network_inheritance: "task_network_policy" as const },
  high_impact: { mode: "approval_required" as const, allowed_actions: [] },
};

const safePolicy = {
  ...backendPolicy,
  preset: "safe_observation" as const,
  network: { ...backendPolicy.network, access: "task_sources" as const, interaction: "observe" as const },
  high_impact: { mode: "forbidden" as const, allowed_actions: [] },
};

const defaultProfiles = [
  { id: "ctf", label: "CTF 解题", description: "CTF", default_goal: "Find the flag", default_mode_config: { mode: "ctf", subtype: "auto" }, default_execution_policy: backendPolicy },
  { id: "penetration_test", label: "渗透测试", description: "Pentest", default_goal: "Test the target", default_mode_config: { mode: "penetration_test", depth: "reconnaissance", included_scopes: [], exclusions: [], rules_of_engagement: "" }, default_execution_policy: safePolicy },
  { id: "incident_response", label: "应急响应", description: "IR", default_goal: "Investigate", default_mode_config: { mode: "incident_response", phase: "triage" }, default_execution_policy: safePolicy },
  { id: "vulnerability_research", label: "漏洞研究", description: "Research", default_goal: "Research", default_mode_config: { mode: "vulnerability_research", depth: "triage" }, default_execution_policy: safePolicy },
  { id: "reverse_analysis", label: "逆向分析", description: "Reverse", default_goal: "Reverse", default_mode_config: { mode: "reverse_analysis", analysis_method: "static_only" }, default_execution_policy: safePolicy },
].map((profile) => ({ ...profile, fields: [], allowed_input_kinds: [], required_conditions: [], recommended_capabilities: [], completion_validator: profile.id, report_sections: [], uses_flag: profile.id === "ctf" }));

vi.mock("../api/tasks", async (importOriginal) => ({ ...await importOriginal<typeof import("../api/tasks")>(), ...mocks }));
vi.mock("../runtime/api-v2", () => ({ runtimeApi: { toolHealth: vi.fn(async () => ({ healthy: true, records: [{ server: "binwalk", configured: true, enabled: true, discovered: true }, { server: "disabled", configured: true, enabled: false, discovered: true }] })) } }));
vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:preview"), revokeObjectURL: vi.fn() });

import { NewTaskPage } from "./NewTaskPage";

async function renderPage(onCreated = vi.fn()) {
  render(<NewTaskPage onCreated={onCreated} />);
  await screen.findByLabelText("任务名称");
}

async function fillRequiredGoalFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("任务名称"), "测试安全任务");
  await user.type(screen.getByLabelText("Objective"), "验证目标并输出可复核的证据");
}

describe("NewTaskPage multimodal input flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchModeProfiles.mockResolvedValue({ schema_version: 1, profiles: defaultProfiles });
  });

  it("shows one prompt composer for text and attachments in step three", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    expect(screen.getByText("任务提示与材料")).toBeInTheDocument();
    expect(screen.getByLabelText("任务提示词")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择文件" })).toBeInTheDocument();
    expect(screen.queryByText("Hint 附件")).toBeNull();
    expect(screen.queryByLabelText("目标 URL")).toBeNull();
    expect(screen.queryByText("代码仓库")).toBeNull();
    expect(screen.queryByText(/MCP Resource|MCP Tool/)).toBeNull();
  });

  it("keeps step four limited to execution boundaries", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: /执行边界/ }));
    expect(screen.getByLabelText("网络访问范围")).toBeInTheDocument();
    expect(screen.getByLabelText("本地计算")).toBeInTheDocument();
    expect(screen.getByLabelText("高影响动作")).toBeInTheDocument();
    expect(screen.queryByText(/MCP 服务与方法授权/)).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /MCP/ })).toBeNull();
  });

  it("uploads multiple files, renders an image thumbnail, and removes staged assets", async () => {
    const user = userEvent.setup();
    await renderPage();
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
    await renderPage();
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    await user.upload(input, new File(["x"], "large.bin"));
    expect(await screen.findByRole("alert")).toHaveTextContent("large.bin: File exceeds the 32 MB limit");
    expect(screen.getByText("失败")).toBeInTheDocument();
  });

  it("summarizes only globally available MCP services and submits asset ids", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    await renderPage(onCreated);
    await fillRequiredGoalFields(user);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    await user.upload(input, new File(["task"], "task.txt", { type: "text/plain" }));
    await screen.findByText("已上传");
    await user.type(screen.getByLabelText("任务提示词"), "Analyze carefully");
    await user.click(screen.getByRole("button", { name: /创建摘要/ }));
    expect(await screen.findByText("binwalk")).toBeInTheDocument();
    expect(await screen.findByText(/2 个可供 Worker 按需检索/)).toBeInTheDocument();
    expect(await screen.findByTestId("preflight-passed")).toHaveTextContent("全部检查通过");
    expect(screen.queryByText("disabled")).toBeNull();
    await user.click(screen.getByRole("button", { name: "创建任务并开始" }));
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalledWith(expect.objectContaining({
      input: { text: "Analyze carefully", fileIds: [`asset_${"a".repeat(32)}`] },
      preflightFingerprint: "f".repeat(64),
    })));
    const submitted = mocks.createTask.mock.calls[0][0] as Record<string, unknown>;
    expect(submitted).not.toHaveProperty("agentModels");
    expect(submitted).not.toHaveProperty("mcp_servers");
    expect(submitted).not.toHaveProperty("targets");
    expect(onCreated).toHaveBeenCalledWith("task_created");
  });

  it("shows shared Skills without binding them to the task request", async () => {
    const user = userEvent.setup();
    await renderPage();
    await fillRequiredGoalFields(user);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    await user.type(screen.getByLabelText("任务提示词"), "Inspect the web target");
    await user.click(screen.getByRole("button", { name: "团队和模型" }));
    await screen.findByText("web-recon");
    expect(screen.getByText("binary-triage")).toBeInTheDocument();
    expect(screen.getByText(/不再绑定场景或角色/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "管理 Skill" })).toHaveAttribute("href", "/settings/skills");

    await user.click(screen.getByRole("button", { name: /创建摘要/ }));
    await user.click(screen.getByRole("button", { name: "创建任务并开始" }));
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalled());
    expect(mocks.createTask.mock.calls[0][0]).not.toHaveProperty("selectedSkills");
  });

  it("allows a prompt without requiring an attachment", async () => {
    const user = userEvent.setup();
    await renderPage();
    await fillRequiredGoalFields(user);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    await user.type(screen.getByLabelText("任务提示词"), "Review the supplied target and explain the first verification step.");
    await user.click(screen.getByRole("button", { name: /创建摘要/ }));
    await screen.findByTestId("preflight-passed");
    await user.click(screen.getByRole("button", { name: "创建任务并开始" }));
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalledWith(expect.objectContaining({
      input: { text: "Review the supplied target and explain the first verification step.", fileIds: [] },
    })));
  });

  it("explains preflight blockers and routes the user to the missing field", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.type(screen.getByLabelText("Objective"), "取得目标 flag");
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    await user.type(screen.getByLabelText("任务提示词"), "分析目标并尝试绕过过滤");
    await user.click(screen.getByRole("button", { name: /创建摘要/ }));

    expect(await screen.findByText("填写任务名称")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "任务目标" })).toHaveAttribute("data-complete", "false");
    const submit = screen.getByRole("button", { name: "创建任务并开始" });
    expect(submit).toBeEnabled();
    expect(mocks.preflightTask).not.toHaveBeenCalled();

    await user.click(submit);
    expect(await screen.findByLabelText("任务名称")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("启动前还需完成：填写任务名称");

    await user.type(screen.getByLabelText("任务名称"), "管道符绕过过滤");
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("blocks creation when authoritative preflight fails", async () => {
    mocks.preflightTask.mockRejectedValueOnce(new Error("Model verification is stale"));
    const user = userEvent.setup();
    await renderPage();
    await fillRequiredGoalFields(user);
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    await user.type(screen.getByLabelText("任务提示词"), "Inspect the target");
    await user.click(screen.getByRole("button", { name: /创建摘要/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Model verification is stale");
    expect(screen.getByRole("button", { name: "创建任务并开始" })).toBeDisabled();
    expect(mocks.createTask).not.toHaveBeenCalled();
  });

  it("reset clears uploaded state and staging", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: /任务提示与材料/ }));
    await user.upload(document.querySelector<HTMLInputElement>('input[type="file"]')!, new File(["x"], "old.txt"));
    await screen.findByText("old.txt");
    await user.click(screen.getByRole("button", { name: "重置" }));
    expect(screen.queryByText("old.txt")).toBeNull();
    expect(mocks.deleteStagedInput).toHaveBeenCalled();
  });

  it("uses backend policy defaults as the only scene source", async () => {
    mocks.fetchModeProfiles.mockResolvedValueOnce({ schema_version: 1, profiles: defaultProfiles });
    const user = userEvent.setup();
    await renderPage();
    await user.click(await screen.findByRole("button", { name: /执行边界/ }));
    expect(screen.getByLabelText("执行策略")).toHaveValue("safe_observation");
  });

  it("marks edited policy details as custom", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: /执行边界/ }));
    await user.selectOptions(screen.getByLabelText("网络访问范围"), "disabled");
    expect(screen.getByLabelText("执行策略")).toHaveValue("custom");
  });

  it("allows an explicit custom CIDR rule", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: /执行边界/ }));
    await user.selectOptions(screen.getByLabelText("网络访问范围"), "custom");
    await user.type(screen.getByLabelText("自定义 CIDR"), "198.18.0.0/15");
    expect(screen.getByLabelText("执行策略")).toHaveValue("custom");
    expect(screen.getByLabelText("自定义 CIDR")).toHaveValue("198.18.0.0/15");
  });
});
