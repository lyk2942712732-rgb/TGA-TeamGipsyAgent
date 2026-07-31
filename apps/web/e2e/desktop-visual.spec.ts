import { expect, test, type Page, type Route } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const outputRoot = process.env.TGA_DESKTOP_SCREENSHOTS ?? join(process.cwd(), "test-results", "desktop-visual");
const now = "2026-07-31T06:30:00Z";

const task = {
  task_id: "task-visual", name: "Acme Portal 安全验证", mode: "penetration_test", target_summary: "portal.acme.test",
  created_at: "2026-07-31T02:00:00Z", updated_at: now, status: "awaiting_approval", turn_count: 8, max_turns: 30,
  active_solvers: 4, pending_approvals: 1, needs_attention: true, intent_total: 8, intent_completed: 4,
  latest_event: { seq: 18, type: "APPROVAL_REQUESTED", created_at: now }, flags: 0, findings: 3, artifacts: 7,
};

const detail = {
  schema_version: 6, task_id: task.task_id,
  task: { id: task.task_id, name: task.name, mode: task.mode, goal: "验证授权目标的身份边界并提供可复核证据", task_entry_url: "https://portal.acme.test", schema_version: 6 },
  task_spec: { task_id: task.task_id, objective: "验证授权目标的身份边界并提供可复核证据", instructions: [{ id: "i1", content: "仅验证明确授权的目标" }], constraints: [{ id: "c1", content: "禁止修改生产数据" }], success_criteria: [{ id: "s1", content: "Finding 必须关联确认的 Evidence Claim" }], resources: [{ id: "r1", type: "url" }] },
  lifecycle: { created_at: task.created_at, updated_at: now, status: task.status, turn_count: 8, max_turns: 30, active_solvers: 4, pending_approvals: 1, intent_total: 8, intent_completed: 4, flags: 0, findings: 3, artifacts: 7, needs_attention: true, latest_event: task.latest_event },
  input_summary: { prompt_present: true, prompt_preview: "验证登录与会话边界", file_count: 2, files: [], task_entry_url: "https://portal.acme.test" },
  config_snapshot: { mode_config: { mode: "penetration_test", depth: "validation", included_scopes: ["portal.acme.test"] }, execution_policy: { preset: "safe_observation", network: { access: "task_sources", interaction: "observe" }, high_impact: { mode: "approval_required" } }, execution_budget: { token_limit: 120000, tool_call_limit: 500 }, model: { model: "gpt-secure" }, mcp_capabilities: { servers: ["browser"] }, task_common_skills: { names: ["web-recon"] }, agent_prompt: { version: "v1" } },
};

const approval = {
  approval_id: "approval-visual", task_id: task.task_id, task_name: task.name, solver_id: "web-validator", intent_id: "intent-session",
  action_id: "action-cookie-write", action_kind: "tool_call", capability: "http.request", target: "https://portal.acme.test/session/refresh", risk: "active",
  effect: { description: "发送一次受控会话刷新请求", reversibility: "reversible" }, rationale: "确认刷新令牌是否可重复使用", expected_outcome: "返回新的受限会话",
  alternative_analysis: "仅检查已有响应，证据强度较低", alternatives: ["使用缓存响应"], reversibility: "reversible", expires_at: "2026-08-01T06:30:00Z",
  status: "pending", decision_allowed: true, decision_block_reason: null, created_at: now, updated_at: now,
};

function runtimeSnapshot() {
  const solver = (id: string, role: string, status: string, parent: string | null, intent: string | null) => ({
    task_id: task.task_id, solver_id: id, definition_id: `${role}-v2`, orchestration_role: role, specialties: [role === "worker" ? "web" : role],
    parent_solver_id: parent, assigned_intent_id: intent, status, current_summary: intent ? `正在处理 ${intent}` : "协调任务计划",
    model_snapshot: { model: "gpt-secure" }, skill_snapshot: { count: 2, names: ["web-recon", "evidence-method"] },
    tool_policy: { allowed_capabilities: ["http.request", "artifact.inspect"] }, budget_usage: { turns: 2, input_tokens: 1600, output_tokens: 420, tool_calls: 4 }, timestamps: { started_at: task.created_at },
  });
  const intent = (id: string, title: string, status: string, solverId: string | null, dependencies: string[]) => ({ task_id: task.task_id, intent_id: id, kind: "investigate", title, objective: title, status, assigned_solver_id: solverId, dependencies, priority: 2, budget: { turns: 8 }, created_at: task.created_at, updated_at: now });
  return {
    schema_version: 6,
    task: { id: task.task_id, name: task.name, mode: task.mode, goal: detail.task.goal, mode_config: { scope: ["portal.acme.test"], success_criteria: ["confirmed evidence"] } },
    session: { status: "running", supervisor_solver_id: "supervisor", active_solver_count: 4, max_active_workers: 3, task_budget_usage: { turns: 8, input_tokens: 7200, output_tokens: 1800, tool_calls: 18, artifacts: 7 }, stop_reason: null, timestamps: { started_at: task.created_at, updated_at: now }, turn_count: 8, max_turns: 30 },
    team: { task_id: task.task_id, status: "running", supervisor_solver_id: "supervisor", max_active_workers: 3, max_total_solvers: 8, active_solver_count: 4, solver_ids: ["supervisor", "recon-worker", "web-validator", "reviewer", "reporter"], version: 3, timestamps: {} },
    solvers: [solver("supervisor", "supervisor", "running", null, null), solver("recon-worker", "worker", "running", "supervisor", "intent-map"), solver("web-validator", "worker", "awaiting_approval", "supervisor", "intent-session"), solver("reviewer", "reviewer", "reviewing", "supervisor", "intent-review"), solver("reporter", "reporter", "waiting", "supervisor", "intent-report")],
    intents: [intent("intent-map", "梳理公开攻击面", "running", "recon-worker", []), intent("intent-session", "验证会话刷新边界", "awaiting_approval", "web-validator", ["intent-map"]), intent("intent-review", "复核证据链", "reviewing", "reviewer", ["intent-session"]), intent("intent-report", "生成最终报告", "pending", "reporter", ["intent-review"]), intent("intent-done", "确认授权范围", "completed", "supervisor", [])],
    worker_results: [{ result_id: "result-map", solver_id: "recon-worker", intent_id: "intent-map", status: "submitted", summary: "发现 12 个受控端点", artifact_ids: ["artifact-http"], evidence_claim_ids: ["claim-version"], knowledge_ids: [], finding_ids: ["finding-session"], limitations: [], budget_usage: {} }],
    global_plan: { version: 3, success_criteria: ["confirmed evidence", "reviewer approval"] },
    knowledge: [{ knowledge_id: "knowledge-one", scope: "task", target_id: null, status: "verified", kind: "fact", content_preview: "目标使用短期访问令牌", content_sha256: "aa", created_by_solver_id: "reviewer", created_at: now }],
    artifacts: [{ artifact_id: "artifact-http", intent_id: "intent-map", kind: "http_exchange", media_type: "application/json", tool: "http.request", target: "https://portal.acme.test/api/version", sha256: "a".repeat(64), created_at: now }],
    evidence_claims: [{ claim_id: "claim-version", statement_preview: "响应头证明会话网关版本", artifact_id: "artifact-http", locator: { kind: "header", name: "x-gateway-version" }, status: "confirmed", created_by_solver_id: "recon-worker", reviewed_by_solver_id: "reviewer", created_at: now, reviewed_at: now }],
    findings: [{ finding_id: "finding-session", title: "刷新令牌可重复使用", description_preview: "受控测试显示旧刷新令牌未立即失效", target: "portal.acme.test", severity: "high", status: "confirmed", evidence_claim_ids: ["claim-version"], created_by_solver_id: "web-validator", created_at: now, reviewed_at: now }],
    actions: [{ action_id: approval.action_id, solver_id: approval.solver_id, intent_id: approval.intent_id, capability: approval.capability, target: approval.target, risk: approval.risk, effect: approval.effect, arguments: {}, status: "pending_approval", summary: approval.rationale, artifact_ids: [], created_at: now, updated_at: now }],
    approvals: [{ approval_id: approval.approval_id, solver_id: approval.solver_id, intent_id: approval.intent_id, action_id: approval.action_id, action: { capability: approval.capability, target: approval.target, expected_outcome: approval.expected_outcome }, risk: approval.risk, effect: approval.effect, reason: approval.rationale, alternatives: approval.alternatives, deadline: approval.expires_at, status: "pending", created_at: now, updated_at: now }],
    retrieval_runs: [{ retrieval_run_id: "retrieval-one", owner_scope: "task", task_id: task.task_id, solver_id: "recon-worker", intent_id: "intent-map", index_snapshot_id: "index-v12", method: "hybrid", query_preview: "session refresh", hit_count: 6, created_at: now }],
    events: [{ schema_version: 6, id: "event-18", task_id: task.task_id, seq: 18, type: "APPROVAL_REQUESTED", solver_id: approval.solver_id, intent_id: approval.intent_id, payload: { action_id: approval.action_id, summary: approval.rationale }, created_at: now }],
    events_page: { after_seq: 0, next_after_seq: 18, has_more: false }, latest_seq: 18,
  };
}

const catalogItems: Record<string, Record<string, unknown>[]> = {
  resources: [
    { kind: "artifacts", id: "artifact-http", task_id: task.task_id, title: "API Version Response", status: "available", raw: { kind: "http_exchange", solver_id: "recon-worker", intent_id: "intent-map", media_type: "application/json", sha256: "a".repeat(64), created_at: now } },
    { kind: "evidence", id: "claim-version", task_id: task.task_id, title: "响应头证明会话网关版本", status: "confirmed", raw: { artifact_id: "artifact-http", locator: { header: "x-gateway-version" }, created_by_solver_id: "recon-worker", reviewed_by_solver_id: "reviewer", created_at: now } },
    { kind: "findings", id: "finding-session", task_id: task.task_id, title: "刷新令牌可重复使用", status: "confirmed", raw: { severity: "high", target: "portal.acme.test", evidence_claim_ids: ["claim-version"], created_by_solver_id: "web-validator", created_at: now } },
    { kind: "knowledge", id: "knowledge-one", task_id: task.task_id, title: "目标使用短期访问令牌", status: "verified", raw: { kind: "fact", scope: "task", created_by_solver_id: "reviewer", created_at: now } },
  ],
  reports: [{ id: "report-visual", task_id: task.task_id, title: "Acme Portal 安全验证报告", mode: task.mode, version: "v3", status: "reviewing", finding_count: 3, created_at: "2026-07-31T05:00:00Z", updated_at: now }],
  "knowledge-bases": [{ id: "kb-security", name: "安全工程知识库", kind: "Hybrid", scope: "workspace", document_count: 128, source_count: 6, index_version: "snapshot-v12", status: "available", last_sync_at: now }],
  teams: [{ id: "team-web", name: "Web 验证标准团队", modes: ["penetration_test", "ctf"], supervisor: "security-supervisor", default_roles: ["worker", "reviewer", "reporter"], max_parallel_solvers: 3, max_total_solvers: 8, status: "enabled", updated_at: now, required_workers: ["recon-worker", "web-validator"], available_workers: ["browser-worker", "api-worker"], reviewer: "evidence-reviewer", reporter: "security-reporter", spawn_rules: ["按 Intent 专长生成 Worker"], completion_policy: { reviewer_required: true }, default_execution_policy: { preset: "safe_observation" }, version: "v4" }],
  solvers: [{ id: "web-validator-v2", display_name: "Web Validator", version: "2.4.0", role: "worker", modes: ["penetration_test", "ctf"], specialties: ["session", "authorization", "api"], status: "enabled", completion_authority: "submit_to_reviewer", tool_policy_profile: "active-with-approval", default_budget: { turns: 12, tool_calls: 60 }, system_prompt_template: "验证授权目标，所有结论必须关联可复核证据。", required_capabilities: ["http.request", "artifact.inspect"], allowed_tool_groups: ["network", "artifact"], default_skill_tags: ["web", "evidence"], required_skill_names: ["web-recon"], accepted_intent_kinds: ["investigate", "validate"], output_contract: { finding: "evidence_required" }, content_sha256: "b".repeat(64) }],
  policies: [{ id: "safe-observation", name: "安全观察策略", mode: "Penetration Test", status: "available", description: "默认限制到任务来源并要求高影响操作审批", network: { access: "task_sources", deny_private_networks: true, deny_loopback: true, deny_link_local: true, deny_cloud_metadata: true, rate_limit_per_minute: "30/min", concurrency: "3", request_timeout_seconds: "30s" }, local_compute: { mode: "isolated" }, high_impact: { mode: "approval_required" }, budget: { max_runtime_seconds: "2h", token_limit: "120k", tool_call_limit: "500", artifact_limit: "1GB" } }],
};

async function fulfillJson(route: Route, json: unknown) { await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(json) }); }

async function installFixtures(page: Page) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (path === "/api/v2/dashboard") return fulfillJson(route, { schema_version: 1, generated_at: now, metrics: { running_tasks: 4, pending_approvals: 3, awaiting_user_input: 2, blocked_tasks: 1, active_solvers: 12 }, needs_attention: [{ id: approval.approval_id, kind: "approval", task_id: task.task_id, task_name: task.name, title: "会话刷新请求待审批", description: approval.rationale, status: "pending", risk: "active", action_id: approval.action_id, updated_at: now }, { id: "input-1", kind: "user_input", task_id: "task-ir", task_name: "主机取证分析", title: "需要补充磁盘镜像", description: "Reporter 需要完整证据源", status: "blocked", updated_at: now }], active_tasks: [task, { ...task, task_id: "task-reverse", name: "Firmware 逆向分析", mode: "reverse_engineering", status: "running", pending_approvals: 0, findings: 1, artifacts: 4 }], recent_completed: [{ ...task, task_id: "task-done", name: "API 配置审计", status: "completed", intent_completed: 8 }], system_status: [{ id: "api", label: "API", status: "healthy", detail: "响应正常", available: true }, { id: "models", label: "Model Providers", status: "available", detail: "Provider 已验证", available: true }], unavailable_metrics: [] });
    if (path === "/api/v2/tasks" && request.method() === "GET") return fulfillJson(route, { tasks: [task, { ...task, task_id: "task-reverse", name: "Firmware 逆向分析", mode: "reverse_engineering", status: "running", needs_attention: false, pending_approvals: 0 }, { ...task, task_id: "task-done", name: "API 配置审计", status: "completed", needs_attention: false, pending_approvals: 0, intent_completed: 8 }], offset: 0, limit: 100, total: 3, next_offset: null });
    if (path === `/api/v2/tasks/${task.task_id}`) return fulfillJson(route, detail);
    if (path === `/api/v2/tasks/${task.task_id}/team`) return fulfillJson(route, { task_id: task.task_id, team: runtimeSnapshot().team, solvers: runtimeSnapshot().solvers });
    if (path === `/api/v2/tasks/${task.task_id}/inputs`) return fulfillJson(route, { task_goal: detail.task.goal, prompt: "验证登录与会话边界", files: [{ id: "input-1", label: "scope.md", mime_type: "text/markdown", size: 2180 }], task_entry_url: detail.task.task_entry_url });
    if (path === `/api/v2/tasks/${task.task_id}/evidence`) return fulfillJson(route, { task_id: task.task_id, artifacts: { offset: 0, limit: 100, total: 1, items: runtimeSnapshot().artifacts }, evidence_claims: { offset: 0, limit: 100, total: 1, items: runtimeSnapshot().evidence_claims }, findings: { offset: 0, limit: 100, total: 1, items: runtimeSnapshot().findings } });
    if (path === `/api/v2/tasks/${task.task_id}/timeline`) return fulfillJson(route, { events: runtimeSnapshot().events, latest_seq: 18, has_more: false });
    if (path === `/api/v2/tasks/${task.task_id}/session`) return fulfillJson(route, runtimeSnapshot());
    if (path === `/api/v2/tasks/${task.task_id}/events`) return fulfillJson(route, { events: [], latest_seq: 18, has_more: false });
    if (path === `/api/v2/tasks/${task.task_id}/events/stream`) return route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: heartbeat\ndata: {\"latest_seq\":18}\n\n" });
    if (path === "/api/v2/approvals") return fulfillJson(route, { schema_version: 1, offset: 0, limit: 12, total: 1, next_offset: null, items: [approval], filters: {} });
    if (path === "/api/v2/mode-profiles") return fulfillJson(route, { schema_version: 5, profiles: [] });
    if (path === "/api/v2/tasks/skill-preview") return fulfillJson(route, { selector: "visual", fingerprint: "f".repeat(64), count: 1, skills: [{ name: "web-recon", version: "2", origin: "builtin", capabilities: ["http.request"], tags: ["web"], content_sha256: "c".repeat(64), selection_reasons: ["场景与目标匹配"] }] });
    if (path === "/api/v2/tasks/preflight") return fulfillJson(route, { fingerprint: "f".repeat(64), task_id: task.task_id, checks: [{ id: "model", status: "passed", detail: "模型与工具协议已验证" }], skill_snapshot: { selector: "visual", count: 1, content_sha256: "c".repeat(64) }, mcp_catalog_version: "v2", model_verification_id: "verify-1" });
    if (path.startsWith("/api/v2/catalog/")) { const kind = path.slice("/api/v2/catalog/".length); const items = catalogItems[kind] ?? []; return fulfillJson(route, { supported: true, reason: "当前 Catalog 为只读", kind, items, total: items.length }); }
    if (path === "/api/v2/settings/skills") return fulfillJson(route, { schema_version: 3, skills: [{ name: "web-recon", modes: ["ctf", "penetration_test"], capabilities: ["http.request", "artifact.inspect"], tags: ["recon", "web"], version: "2.1", source: "builtin", summary: "梳理 Web 攻击面并保存可复核证据", editable: true }, { name: "evidence-method", modes: ["penetration_test", "incident_response"], capabilities: ["artifact.inspect"], tags: ["evidence", "report"], version: "1.4", source: "builtin", summary: "构建 Evidence Claim 与 Finding 链路", editable: true }] });
    if (path.startsWith("/api/v2/settings/skills/")) { const name = decodeURIComponent(path.split("/").pop()!); return fulfillJson(route, { skill: { name, modes: ["ctf", "penetration_test"], capabilities: ["http.request", "artifact.inspect"], tags: ["recon", "web"], version: "2.1", source: "builtin", summary: "梳理 Web 攻击面并保存可复核证据", editable: true, body: "# Instructions\n\n1. 确认授权范围。\n2. 保存每次请求与响应。\n3. 所有结论关联 Evidence Claim。" } }); }
    if (path === "/api/v2/capabilities") return fulfillJson(route, { capabilities: [{ name: "http.request", availability: "available", risk: "active", modes: ["ctf", "penetration_test"] }, { name: "artifact.inspect", availability: "available", risk: "passive", modes: ["ctf", "penetration_test", "incident_response"] }, { name: "workspace.read", availability: "available", risk: "passive", modes: ["reverse_engineering", "incident_response"] }], tools: { availability: "healthy", tools: [{ tool_id: "browser", provider_name: "mcp__browser__navigate", risk: "active", methods: [{ name: "navigate", description: "打开授权 URL" }] }] } });
    if (path === "/api/v2/tools/health") return fulfillJson(route, { configured: true, status: "healthy", checked_at: now, records: [{ server: "browser", configured: true, enabled: true, reachable: true, discovered: true, runnable: true, tools: 3, transport: "stdio", protocol_version: "2025-06-18", last_call_at: now }] });
    if (path === "/api/v2/mcp/servers") return fulfillJson(route, { servers: [{ id: "browser", config: { enabled: true, transport: "stdio", stdio: { source: "docker_image", image: "tga/browser-mcp:latest" } }, status: { server: "browser", configured: true, enabled: true, reachable: true, discovered: true, runnable: true, tools: 3, transport: "stdio" } }] });
    if (path === "/api/v2/settings/llm") return fulfillJson(route, { configured: true, base_url: "https://models.acme.test/v1", model: "gpt-secure", api_key_set: true, supports_vision: true, max_output_tokens: 8192, timeout_seconds: 90, temperature: 0.2, reasoning_mode: "enabled", verification_status: "verified", verification: { status: "verified", verified_at: now, capabilities: { tools: true, vision: true }, last_error: null } });
    if (path === "/api/health") return fulfillJson(route, { status: "ok", service: "tga-api" });
    return fulfillJson(route, {});
  });
}

const pages = [
  { name: "dashboard", path: "/", heading: "运营总览" },
  { name: "tasks", path: "/tasks", heading: "任务" },
  { name: "create-task", path: "/tasks/new", heading: "新建任务" },
  { name: "task-detail", path: `/tasks/${task.task_id}`, heading: task.name },
  { name: "runtime-workbench", path: `/tasks/${task.task_id}/runtime?tab=work-items&solver=web-validator`, heading: task.name },
  { name: "approvals", path: "/approvals?status=pending", heading: "全局审批中心" },
  { name: "resources", path: "/resources", heading: "资源" },
  { name: "reports", path: "/reports", heading: "报告" },
  { name: "knowledge-bases", path: "/knowledge-bases", heading: "知识库" },
  { name: "team-templates", path: "/settings/teams", heading: "团队模板" },
  { name: "solvers", path: "/settings/solvers", heading: "Solver Definitions" },
  { name: "skills", path: "/settings/skills", heading: "Skills" },
  { name: "tools-mcp", path: "/settings/tools", heading: "Tools & MCP" },
  { name: "models", path: "/settings/models", heading: "Models" },
  { name: "policies-budgets", path: "/settings/policies", heading: "策略与预算" },
  { name: "system-status", path: "/system", heading: "系统状态" },
] as const;

test.beforeEach(async ({ page }) => installFixtures(page));

for (const item of pages) {
  test(`${item.name} desktop visual`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(item.path);
    await expect(page.getByRole("heading", { name: item.heading, exact: true }).first()).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await mkdir(outputRoot, { recursive: true });
    await page.screenshot({ path: join(outputRoot, `${item.name}.png`), fullPage: false, animations: "disabled" });
  });
}

for (const viewport of [{ width: 1280, height: 900 }, { width: 1920, height: 1080 }]) {
  test(`all desktop pages avoid horizontal overflow at ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    for (const item of pages) {
      await page.goto(item.path);
      await expect(page.getByRole("heading", { name: item.heading, exact: true }).first()).toBeVisible();
      expect(await page.evaluate(() => ({ path: location.pathname, viewport: innerWidth, document: document.documentElement.scrollWidth })), item.name).toEqual(expect.objectContaining({ document: viewport.width }));
    }
  });
}
