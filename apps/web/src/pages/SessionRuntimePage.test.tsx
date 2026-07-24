import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SessionRuntimePage, redact } from "./SessionRuntimePage";
import type { RuntimeSnapshot } from "../runtime/event-types";

const control = vi.fn();
const useSessionRuntime = vi.fn();
vi.mock("../api/runtime", () => ({ runtimeApi: { control: (...args: unknown[]) => control(...args), hint: vi.fn(), reportUrl: () => "/report", artifactUrl: (_task: string, id: string) => `/artifact/${id}` } }));
vi.mock("../runtime/session-store", () => ({ useSessionRuntime: (...args: unknown[]) => useSessionRuntime(...args) }));

const snapshot = (): RuntimeSnapshot => ({
  task: { id: "task", name: "本地证据任务", mode: "ctf", prompt: "读取本地证据", files: [], model_snapshot: { provider: "openai-compatible", model: "frozen-task-model", verification_id: "verify_task", verified_at: "2026-07-23T00:00:00Z" } },
  session: { status: "completed", turn_count: 2, max_turns: 8, stop_reason: "finish_accepted", started_at: "2026-07-23T00:00:00Z", finished_at: "2026-07-23T00:00:04Z" },
  solvers: [{ id: "agent", role: "main", status: "completed", model_name: "provider-model" }], challenge: { status: "solved", status_reason: "" },
  runtime: { memory: [], strategy_cards: [{ id: "card", task_id: "task", title: "读取任务输入", summary: "从输入读取目标值", claims: [], prerequisites: [], target_version_checks: [], status: "succeeded", active_step_id: null, sources: [], steps: [{ id: "step", title: "读取输入", instructions: "", expected_request: "input_read", success_marker: "artifact", failure_conditions: [], risk: "passive", status: "succeeded", action_ids: ["action"], evidence_artifact_ids: ["artifact"], last_result: "读取成功" }] }] },
  actions: [{ id: "action", capability: "input_read", target: "input", status: "succeeded", risk: "passive", strategy_card_id: "card", strategy_step_id: "step", rationale: "读取用户提供的本地文件", expected_outcome: "生成任务证据", artifact_ids: ["artifact"], authorization: { allowed: true }, summary: "读取成功" }],
  flags: [{ value: "CTF{verified}", evidence_artifact_id: "artifact" }], findings: [], artifacts: [{ id: "artifact", kind: "tool_output", path: "artifact.txt", sha256: "1234567890abcdef1234", tool: "input_read", target: "input", provenance: { source: "user_upload" } }],
  events: [
    { id: "1", task_id: "task", seq: 1, type: "MESSAGE_START", payload: { turn: 1 }, created_at: "2026-07-23T00:00:01Z" },
    { id: "2", task_id: "task", seq: 2, type: "FINISH_REJECTED", payload: { turn: 1, validator_code: "EVIDENCE_REQUIRED", missing: ["task-owned Artifact"] }, created_at: "2026-07-23T00:00:02Z" },
    { id: "3", task_id: "task", seq: 3, type: "MESSAGE_START", payload: { turn: 2 }, created_at: "2026-07-23T00:00:03Z" },
    { id: "4", task_id: "task", seq: 4, type: "TOOL_EXECUTION_START", payload: { turn: 2, action_id: "action", tool_name: "input_read", execution_location: "Input Store" }, created_at: "2026-07-23T00:00:03Z" },
    { id: "5", task_id: "task", seq: 5, type: "TOOL_EXECUTION_END", payload: { turn: 2, action_id: "action", tool_name: "input_read", status: "succeeded", summary: "读取成功", execution_location: "Input Store", artifact_ids: ["artifact"] }, created_at: "2026-07-23T00:00:04Z" },
    { id: "6", task_id: "task", seq: 6, type: "FINISH_ACCEPTED", payload: { turn: 2, summary: "已完成", evidence_artifact_ids: ["artifact"], terminal: true }, created_at: "2026-07-23T00:00:04Z" },
    { id: "7", task_id: "task", seq: 7, type: "AGENT_FINISHED", payload: { turn: 2, summary: "已完成", coverage: ["本地输入"], limitations: ["无网络目标"], evidence_artifact_ids: ["artifact"] }, created_at: "2026-07-23T00:00:04Z" },
  ], latest_seq: 7,
});

describe("SessionRuntimePage", () => {
  beforeEach(() => { control.mockReset(); useSessionRuntime.mockReturnValue({ snapshot: snapshot(), connection: "live", error: null, refresh: vi.fn() }); });
  it("shows turn-grouped ReAct facts and governance location", () => { render(<SessionRuntimePage taskId="task" mode="runtime" onReplay={vi.fn()} />); expect(screen.getAllByTestId("react-turn")).toHaveLength(2); expect(screen.getByTitle("frozen-task-model")).toBeInTheDocument(); fireEvent.click(screen.getByRole("button", { name: /第 01 轮/ })); expect(screen.getByText("task-owned Artifact")).toBeInTheDocument(); const locations = screen.getAllByTestId("execution-location"); expect(locations).toHaveLength(2); locations.forEach((item) => expect(item).toHaveTextContent("输入存储")); expect(screen.getByText("第 02 轮")).toBeInTheDocument(); expect(screen.getByText("由 input read 生成")).toBeInTheDocument(); });
  it("shows a final result only after accepted and finished events", () => { render(<SessionRuntimePage taskId="task" mode="runtime" onReplay={vi.fn()} />); fireEvent.click(screen.getByRole("button", { name: "最终结果" })); expect(screen.getByTestId("final-result")).toHaveTextContent("已确认最终结果"); expect(screen.getByText("CTF{verified}")).toBeInTheDocument(); });
  it("localizes strategy memory kinds and deterministic observer guidance", () => {
    const value = snapshot();
    value.runtime.memory = [{ id: "memory", kind: "failure_boundary", content: "Consecutive failures require a new diagnosis before retry: HTTP 404 from https://example.test/", artifact_ids: [], source: "observer" }];
    useSessionRuntime.mockReturnValue({ snapshot: value, connection: "live", error: null, refresh: vi.fn() });
    render(<SessionRuntimePage taskId="task" mode="runtime" onReplay={vi.fn()} />);
    expect(screen.getByText("失败边界")).toBeInTheDocument();
    expect(screen.getByText("连续失败，重试前必须重新诊断：HTTP 404 from https://example.test/")).toBeInTheDocument();
  });
  it("restores controls and exposes a rejected cancellation", async () => { const value = snapshot(); value.session.status = "running"; useSessionRuntime.mockReturnValue({ snapshot: value, connection: "live", error: null, refresh: vi.fn() }); control.mockRejectedValueOnce(new Error("manager rejected cancellation")); render(<SessionRuntimePage taskId="task" mode="runtime" onReplay={vi.fn()} />); fireEvent.click(screen.getByRole("button", { name: /取消/ })); fireEvent.click(screen.getByRole("button", { name: "确认取消" })); await waitFor(() => expect(screen.getByText("manager rejected cancellation")).toBeInTheDocument()); });
  it("shows a rejected control response even when the request returns 200", async () => { const value = snapshot(); value.session.status = "running"; useSessionRuntime.mockReturnValue({ snapshot: value, connection: "live", error: null, refresh: vi.fn() }); control.mockResolvedValueOnce({ accepted: false, reason: "current transition is not allowed" }); render(<SessionRuntimePage taskId="task" mode="runtime" onReplay={vi.fn()} />); fireEvent.click(screen.getByRole("button", { name: /暂停/ })); await waitFor(() => expect(screen.getByText("current transition is not allowed")).toBeInTheDocument()); });
  it("renders a pending high-impact action and sends the durable approval control", async () => {
    const value = snapshot();
    value.session.status = "awaiting_approval";
    value.actions = [{ id: "approve-me", capability: "http.request", target: "https://example.test/item", status: "pending_approval", risk: "active", rationale: "删除测试资源", expected_outcome: "资源被删除", alternative_analysis: "GET 无法验证删除行为", effect: { scope: "target", persistence: "persistent", reversibility: "irreversible", category: "resource_delete", description: "删除测试资源" }, approval_expires_at: "2099-01-01T00:00:00Z", artifact_ids: [], arguments: { method: "DELETE", body: { redacted: true } } }];
    useSessionRuntime.mockReturnValue({ snapshot: value, connection: "live", error: null, refresh: vi.fn() });
    control.mockResolvedValueOnce({ accepted: true, status: "running" });
    render(<SessionRuntimePage taskId="task" mode="runtime" onReplay={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "需要审批的操作" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批准并执行" }));
    await waitFor(() => expect(control).toHaveBeenCalledWith("task", "approve_action", "approve-me"));
  });
  it("shows a top-level structured runtime error without dropping its message", () => {
    const value = snapshot();
    value.session = { ...value.session, status: "blocked", stop_reason: "ISOLATED_RUNTIME_UNAVAILABLE" };
    value.events.push({ id: "runtime-error", task_id: "task", seq: 8, type: "RUNTIME_ERROR", payload: { code: "ISOLATED_RUNTIME_UNAVAILABLE", phase: "process", message: "Docker runtime is unavailable", retryable: true }, created_at: "2026-07-23T00:00:05Z" });
    useSessionRuntime.mockReturnValue({ snapshot: value, connection: "live", error: null, refresh: vi.fn() });
    render(<SessionRuntimePage taskId="task" mode="runtime" onReplay={vi.fn()} />);
    expect(screen.getByRole("alert", { name: "运行时错误" })).toHaveTextContent("Docker runtime is unavailable");
  });
  it("redacts credential-shaped text", () => { expect(redact("Authorization=secret password=hunter2")).toBe("Authorization=[REDACTED] password=[REDACTED]"); });
});
