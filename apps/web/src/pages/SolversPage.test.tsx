import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchHostCapabilities: vi.fn(),
  fetchHostCapabilityProfiles: vi.fn(),
  fetchKaliProfiles: vi.fn(),
  fetchSolverDefinitions: vi.fn(),
  fetchSolverKaliHealth: vi.fn(),
  fetchSolverKaliHealthSummary: vi.fn(),
  fetchSolverManifest: vi.fn(),
  checkSolverKaliHealth: vi.fn(),
  updateSolverCapabilities: vi.fn(),
}));

vi.mock("../api/catalog-query-adapter", async (original) => ({
  ...await original<typeof import("../api/catalog-query-adapter")>(),
  ...mocks,
}));

import { SolversPage } from "./SolversPage";

const solver = {
  id: "ctf-pwn-solver",
  version: "1",
  role: "worker" as const,
  specialties: ["pwn"],
  supported_modes: ["ctf"],
  supported_subtypes: ["pwn"],
  system_prompt_template: "Solve the challenge.",
  default_skill_tags: [],
  required_skill_names: [],
  host_capability_profile_id: "worker-default",
  host_capability_overrides: { add: [], remove: [] },
  host_capabilities: [{
    id: "artifact.inspect",
    display_name: "Inspect artifact",
    category: "artifact",
    risk: "passive",
    source: "worker-default",
  }],
  kali: {
    profile_id: "ctf-pwn-v1",
    capabilities: ["kali.exec" as const],
    image_name: "tga/kali-ctf-pwn",
    image_tag: "2026.08",
    image_digest: null,
    allowed_executables: ["gdb"],
    session_executables: ["gdb"],
    network_mode: "disabled",
    limits: { cpu_cores: 2, memory_mb: 4096, timeout_seconds: 300, max_processes: 256 },
    tools: [],
  },
  accepted_intent_kinds: ["exploit_development"],
  output_contract: { name: "worker-result", required_fields: [] },
  default_budget: {},
  completion_authority: "worker_result",
  content_sha256: "a".repeat(64),
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><SolversPage /></QueryClientProvider>);
}

describe("SolversPage capability editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchSolverDefinitions.mockResolvedValue({ items: [solver], total: 1 });
    mocks.fetchSolverKaliHealthSummary.mockResolvedValue({
      items: [{ solver_id: solver.id, requires_kali: true, profile_id: "ctf-pwn-v1", status: "unresolved_digest" }],
      total: 1,
    });
    mocks.fetchSolverKaliHealth.mockResolvedValue({
      solver_id: solver.id,
      requires_kali: true,
      profile_id: "ctf-pwn-v1",
      image: "ghcr.io/team-gipsy/tga-kali-universal@sha256:REPLACE_WITH_RELEASE_DIGEST",
      status: "unresolved_digest",
      image_status: "unresolved_digest",
      runtime_status: "sandboxd_unavailable",
      checked_at: "2026-08-03T14:30:00Z",
      reasons: [{ code: "unresolved_image_digest", message: "image digest has not been resolved" }],
      missing_executables: [],
      image_store: { status: "unknown", error: null },
      toolset: { expected_digest: "5f12", actual_digest: null, status: "not_checked" },
    });
    mocks.checkSolverKaliHealth.mockResolvedValue({});
    mocks.fetchSolverManifest.mockResolvedValue({ host_capabilities: [], kali: null });
    mocks.fetchHostCapabilityProfiles.mockResolvedValue({
      items: [{ id: "worker-default", capability_ids: ["artifact.inspect"] }],
      total: 1,
    });
    mocks.fetchHostCapabilities.mockResolvedValue({
      items: [
        {
          id: "artifact.inspect", display_name: "Inspect artifact", category: "artifact",
          description: "Inspect an artifact.", allowed_roles: ["worker"], risk: "passive",
          input_schema: {}, output_schema: {}, handler_key: "artifact.inspect",
          handler_status: "ready", assigned_solver_count: 1,
          assigned_solver_ids: [solver.id],
        },
        {
          id: "artifact.publish", display_name: "Publish artifact", category: "artifact",
          description: "Publish an artifact.", allowed_roles: ["worker"], risk: "high",
          input_schema: {}, output_schema: {}, handler_key: "artifact.publish",
          handler_status: "ready", assigned_solver_count: 0, assigned_solver_ids: [],
        },
      ],
      total: 2,
    });
    mocks.fetchKaliProfiles.mockResolvedValue({
      items: [{
        id: "ctf-pwn-v1", display_name: "CTF pwn", image_name: "ghcr.io/team-gipsy/tga-kali-universal",
        image_tag: "latest", image_digest: null, image: "ghcr.io/team-gipsy/tga-kali-universal@sha256:REPLACE_WITH_RELEASE_DIGEST",
        image_role: "universal", shared_image_profile_count: 22,
        tools: [], supported_capabilities: ["kali.exec", "kali.session"],
        allowed_executables: ["gdb"], session_executables: ["gdb"], network_mode: "disabled",
        input_mount: "read_only", scratch_mount: "private_read_write",
        shared_artifact_mount: "read_only",
        limits: { cpu_cores: 2, memory_mb: 4096, timeout_seconds: 300, max_processes: 256 },
        enabled: true, assigned_solver_count: 1, assigned_solver_ids: [solver.id],
        config_sha256: "b".repeat(64),
      }],
      total: 1,
    });
    mocks.updateSolverCapabilities.mockResolvedValue(solver);
  });

  it("submits Host overrides with the current Solver revision", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "能力（Tools）" }));
    await user.click(await screen.findByRole("button", { name: "编辑能力" }));
    await user.click(screen.getByRole("checkbox", { name: /artifact\.inspect/ }));
    await user.click(screen.getByRole("checkbox", { name: /artifact\.publish/ }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(mocks.updateSolverCapabilities).toHaveBeenCalledWith(
      solver.id,
      {
        expected_content_sha256: solver.content_sha256,
        host_capability_profile_id: "worker-default",
        host_capability_overrides: {
          add: ["artifact.publish"],
          remove: ["artifact.inspect"],
        },
        kali: { profile_id: "ctf-pwn-v1", capabilities: ["kali.exec"] },
      },
    ));
  });

  it("shows an unresolved digest as unpublished with the real image reference", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findAllByText("未发布")).not.toHaveLength(0);
    await user.click(screen.getByRole("tab", { name: "Kali 信息" }));
    expect(screen.getByText("ghcr.io/team-gipsy/tga-kali-universal@sha256:REPLACE_WITH_RELEASE_DIGEST")).toBeInTheDocument();
    expect(screen.getByText("unresolved_image_digest")).toBeInTheDocument();
    expect(screen.queryByText("健康")).not.toBeInTheDocument();
  });

  it("uses overall runtime status instead of a healthy image status", async () => {
    const user = userEvent.setup();
    mocks.fetchSolverKaliHealthSummary.mockResolvedValue({
      items: [{ solver_id: solver.id, requires_kali: true, profile_id: "ctf-pwn-v1", status: "runtime_unavailable" }], total: 1,
    });
    mocks.fetchSolverKaliHealth.mockResolvedValue({
      solver_id: solver.id, requires_kali: true, profile_id: "ctf-pwn-v1", image: "example/image@sha256:" + "a".repeat(64),
      status: "runtime_unavailable", image_status: "healthy", runtime_status: "sandboxd_unavailable", checked_at: null,
      reasons: [{ code: "runtime_unavailable", message: "sandboxd is unavailable" }], missing_executables: [],
      image_store: { status: "unknown", error: null },
      toolset: { expected_digest: "5f12", actual_digest: "5f12", status: "match" },
    });
    renderPage();

    expect((await screen.findAllByText("Runtime 不可用")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("tab", { name: "Kali 信息" }));
    expect(screen.getByText("健康")).toBeInTheDocument();
    expect(screen.getByText("sandboxd 不可用")).toBeInTheDocument();
  });

  it("shows sandboxd image-store and deferred toolset verification facts", async () => {
    const user = userEvent.setup();
    mocks.fetchSolverKaliHealth.mockResolvedValue({
      solver_id: solver.id, requires_kali: true, profile_id: "ctf-pwn-v1",
      image: "example/image@sha256:" + "a".repeat(64), status: "healthy",
      image_status: "healthy", runtime_status: "sandboxd_available", checked_at: "2026-08-08T00:00:00Z",
      reasons: [], missing_executables: [], image_store: { status: "readable", error: null },
      toolset: { expected_digest: "5f12", actual_digest: null, status: "verified_at_acquire" },
    });
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "Kali 信息" }));
    expect(screen.getByText("可读")).toBeInTheDocument();
    expect(screen.getByText("容器启动时强校验")).toBeInTheDocument();
    expect(screen.getByText("5f12")).toBeInTheDocument();
    expect(screen.getByText("容器启动时读取")).toBeInTheDocument();
  });

  it("refreshes Kali health through the sandboxd-backed API", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "Kali 信息" }));
    await user.click(await screen.findByRole("button", { name: "重新检查" }));
    await waitFor(() => expect(mocks.checkSolverKaliHealth).toHaveBeenCalledWith(solver.id));
    expect(screen.queryByText("深度检查暂不可用")).not.toBeInTheDocument();
  });

  it("filters Solvers by the raw mode value instead of the translated label", async () => {
    const user = userEvent.setup();
    const pentestSolver = {
      ...solver,
      id: "web-api-analyst",
      supported_modes: ["penetration_test"],
      content_sha256: "c".repeat(64),
    };
    mocks.fetchSolverDefinitions.mockResolvedValue({ items: [solver, pentestSolver], total: 2 });
    renderPage();

    expect(await screen.findAllByText("ctf-pwn-solver")).not.toHaveLength(0);
    const modeSelect = screen.getByRole("combobox", { name: "支持模式筛选" });
    expect(screen.getByRole("option", { name: "渗透测试" })).toHaveValue("penetration_test");

    await user.selectOptions(modeSelect, "penetration_test");

    expect(screen.getAllByText("web-api-analyst")).not.toHaveLength(0);
    expect(screen.queryByText("没有匹配的 Solver")).not.toBeInTheDocument();
    expect(screen.queryByText("ctf-pwn-solver")).not.toBeInTheDocument();
  });

  it("keeps Host capabilities inside Tools and places Kali information after Version", async () => {
    const user = userEvent.setup();
    renderPage();

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "基础配置", "Instructions 模板", "能力（Tools）", "默认 Skills", "输出合约", "版本", "Kali 信息",
    ]);
    await user.click(screen.getByRole("tab", { name: "能力（Tools）" }));
    expect(screen.getByText("Host 能力")).toBeInTheDocument();
    expect(screen.getByText("artifact.inspect")).toBeInTheDocument();
  });

  it("only shows capability editing actions on capability tabs", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("tab", { name: "基础配置" });
    expect(screen.queryByRole("button", { name: "编辑能力" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Instructions 模板" }));
    expect(screen.queryByRole("button", { name: "编辑能力" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "能力（Tools）" }));
    expect(screen.getByRole("button", { name: "编辑能力" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "版本" }));
    expect(screen.queryByRole("button", { name: "编辑能力" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Kali 信息" }));
    expect(screen.getByRole("button", { name: "编辑能力" })).toBeInTheDocument();
  });

  it("leaves capability edit mode when switching to a read-only tab", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "能力（Tools）" }));
    await user.click(screen.getByRole("button", { name: "编辑能力" }));
    expect(screen.getByRole("button", { name: "保存" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Instructions 模板" }));
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑能力" })).not.toBeInTheDocument();
  });
});
