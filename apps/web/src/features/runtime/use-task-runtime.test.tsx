import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { normalizeRuntimeSnapshot } from "./models/normalize";
import type { RuntimeEvent } from "./models/types";

const api = vi.hoisted(() => ({
  taskRuntime: vi.fn(), runtimeEvents: vi.fn(),
  streamUrl: vi.fn((taskId: string, afterSeq: number) => `/stream/${taskId}?after_seq=${afterSeq}`),
}));
vi.mock("../../runtime/api-v2", () => ({ runtimeApi: api }));
import { useTaskRuntime } from "./use-task-runtime";

const store = () => normalizeRuntimeSnapshot({ schema_version: 6, task: { id: "task", name: "Task", mode: "ctf" }, session: { status: "running", supervisor_solver_id: null, active_solver_count: 0, max_active_workers: 2, task_budget_usage: {}, stop_reason: null, timestamps: {}, turn_count: 0, max_turns: 20 }, team: { task_id: "task", status: "running", supervisor_solver_id: null, max_active_workers: 2, max_total_solvers: 8, active_solver_count: 0, solver_ids: [], version: 1, timestamps: {} }, solvers: [], intents: [], worker_results: [], global_plan: null, knowledge: [], artifacts: [], evidence_claims: [], findings: [], actions: [], approvals: [], retrieval_runs: [], events: [], events_page: { after_seq: 0, next_after_seq: 0, has_more: false }, latest_seq: 0 });
const event = (seq: number): RuntimeEvent => ({ schemaVersion: 6, id: `event-${seq}`, taskId: "task", seq, type: "FUTURE_EVENT", solverId: null, intentId: null, payload: { payload_version: 1 }, createdAt: "" });

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, (message: MessageEvent<string>) => void>();
  onerror: (() => void) | null = null;
  constructor(readonly url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, listener: EventListenerOrEventListenerObject) { this.listeners.set(type, listener as (message: MessageEvent<string>) => void); }
  close() {}
  emit(value: RuntimeEvent) { this.listeners.get("event")?.({ data: JSON.stringify({ schema_version: value.schemaVersion, id: value.id, task_id: value.taskId, seq: value.seq, type: value.type, solver_id: value.solverId, intent_id: value.intentId, payload: value.payload, created_at: value.createdAt }) } as MessageEvent<string>); }
}
async function flush() { await act(async () => { for (let index = 0; index < 8; index += 1) await Promise.resolve(); }); }

describe("useTaskRuntime", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); FakeEventSource.instances = []; vi.stubGlobal("EventSource", FakeEventSource); });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it("fills an SSE sequence gap from the paginated event query", async () => {
    api.taskRuntime.mockResolvedValue(store());
    api.runtimeEvents.mockResolvedValueOnce({ events: [], latestSeq: 0, hasMore: false }).mockResolvedValueOnce({ events: [event(1), event(2)], latestSeq: 3, hasMore: true }).mockResolvedValueOnce({ events: [event(3)], latestSeq: 3, hasMore: false });
    const { result, unmount } = renderHook(() => useTaskRuntime("task"));
    await flush();
    act(() => FakeEventSource.instances[0].emit(event(3)));
    await flush();
    expect(api.runtimeEvents).toHaveBeenNthCalledWith(2, "task", 0);
    expect(api.runtimeEvents).toHaveBeenNthCalledWith(3, "task", 2);
    expect(Object.keys(result.current.store?.eventsBySeq ?? {}).map(Number)).toEqual([1, 2, 3]);
    unmount();
  });

  it("batches contiguous high-frequency SSE events into one frame", async () => {
    api.taskRuntime.mockResolvedValue(store());
    api.runtimeEvents.mockResolvedValue({ events: [], latestSeq: 0, hasMore: false });
    const { result, unmount } = renderHook(() => useTaskRuntime("task"));
    await flush();
    act(() => { FakeEventSource.instances[0].emit(event(1)); FakeEventSource.instances[0].emit(event(2)); });
    expect(result.current.store?.latestSeq).toBe(0);
    await act(async () => { vi.advanceTimersByTime(16); });
    expect(result.current.store?.latestSeq).toBe(2);
    unmount();
  });
});
