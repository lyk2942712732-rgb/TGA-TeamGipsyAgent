import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SessionRuntimePage, redact } from "./SessionRuntimePage";
import type { RuntimeSnapshot } from "../runtime/event-types";

const sessionRuntime = vi.fn();
const control = vi.fn();
vi.mock("../runtime/session-store", () => ({ useSessionRuntime: (...args: unknown[]) => sessionRuntime(...args) }));
vi.mock("../runtime/api-v2", () => ({ runtimeApi: { control: (...args: unknown[]) => control(...args), hint: vi.fn(), reportUrl: () => "/report", artifactUrl: () => "/artifact" } }));
vi.mock("../components/runtime/ResizableWorkspace", () => ({ ResizableWorkspace: ({ board, timeline, evidence }: { board: React.ReactNode; timeline: React.ReactNode; evidence: React.ReactNode }) => <>{board}{timeline}{evidence}</> }));

function fixture(): RuntimeSnapshot {
  return { task: { id: "task_1", name: "Web CTF", mode: "ctf", target: "http://target", scope: ["target"] }, session: { status: "running", turn_count: 1, max_turns: 48 }, solvers: [], board: { hypotheses: [{ id: "hyp_1", statement: "入口可能可达", attack_class: "recon", entry_point: "/", rationale: "观察到入口", next_test: "请求首页", status: "rejected", confidence: 0.4, attempt_count: 1, evidence_artifact_ids: [], last_result: "首页不包含交互入口" }], memory: [] }, actions: [{ id: "act_1", capability: "http.request", target: "http://target", status: "succeeded", rationale: "验证入口", summary: "真实执行结果", artifact_ids: [] }], flags: [], findings: [], artifacts: [], events: [{ id: "evt_1", task_id: "task_1", seq: 1, type: "ACTION_PROPOSED", payload: { action_id: "act_1", capability: "http.request", target: "http://target" }, created_at: "2026-07-13T00:00:00Z" }, { id: "evt_2", task_id: "task_1", seq: 2, type: "ACTION_FINISHED", payload: { action_id: "act_1", status: "succeeded", summary: "真实执行结果" }, created_at: "2026-07-13T00:00:01Z" }, { id: "evt_3", task_id: "task_1", seq: 3, type: "FLAG_CONFIRMED", payload: { value: "flag{unproven}" }, created_at: "2026-07-13T00:00:02Z" }], latest_seq: 3 };
}

describe("SessionRuntimePage", () => {
  beforeEach(() => { control.mockReset(); sessionRuntime.mockReturnValue({ snapshot: fixture(), connection: "live", error: null, refresh: vi.fn() }); });
  it("keeps a proposed action visibly separate from the finished result", () => { render(<SessionRuntimePage taskId="task_1" mode="runtime" onReplay={vi.fn()} />); expect(screen.getByText("已计划，未执行", { exact: false })).toBeInTheDocument(); expect(screen.getByText("真实执行结果")).toBeInTheDocument(); });
  it("renders a rejected hypothesis as a failure boundary and never confirms an artifact-less flag", () => { const { container } = render(<SessionRuntimePage taskId="task_1" mode="runtime" onReplay={vi.fn()} />); expect(screen.getByText(/失败边界：首页不包含交互入口/)).toBeInTheDocument(); expect(screen.getByText(/确认缺少证据/)).toBeInTheDocument(); fireEvent.click(screen.getByRole("tab", { name: "Confirmed" })); expect(screen.getByText("没有带 artifact provenance 的已确认结论。")).toBeInTheDocument(); expect(container.querySelector(".confirmed-card")).toBeNull(); });
  it("restores controls and exposes the server error when cancellation is rejected", async () => { control.mockRejectedValueOnce(new Error("manager rejected cancellation")); render(<SessionRuntimePage taskId="task_1" mode="runtime" onReplay={vi.fn()} />); fireEvent.click(screen.getByRole("button", { name: "取消" })); fireEvent.click(screen.getByRole("button", { name: "确认取消" })); await waitFor(() => expect(screen.getByText("manager rejected cancellation")).toBeInTheDocument()); expect(screen.getByRole("button", { name: "取消" })).toBeEnabled(); });
  it("redacts sensitive artifact preview values", () => { const preview = redact("Authorization: Bearer abc token=xyz api_key=qwe"); expect(preview).not.toContain("abc"); expect(preview).not.toContain("xyz"); expect(preview).not.toContain("qwe"); });
});
