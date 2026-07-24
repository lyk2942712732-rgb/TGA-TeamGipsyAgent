import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeEvent, RuntimeSnapshot } from "./event-types";

const api = vi.hoisted(() => ({
  session: vi.fn(),
  events: vi.fn(),
  streamUrl: vi.fn((taskId: string, afterSeq: number) => `/stream/${taskId}?after_seq=${afterSeq}`),
}));

vi.mock("./api-v2", () => ({ runtimeApi: api }));

import { useSessionRuntime } from "./session-store";

const event = (seq: number): RuntimeEvent => ({
  id: `event_${seq}`,
  task_id: "task",
  seq,
  type: "SESSION_CONTROLLED",
  payload: { status: "running" },
  created_at: "2026-07-24T00:00:00Z",
});

const snapshot = (taskId = "task"): RuntimeSnapshot => ({
  task: { id: taskId, name: `Task ${taskId}`, mode: "ctf", prompt: "inspect", files: [] },
  session: { status: "created", turn_count: 0, max_turns: 8 },
  solvers: [],
  challenge: { status: "unknown", status_reason: "" },
  runtime: { memory: [], strategy_cards: [] },
  actions: [],
  flags: [],
  findings: [],
  artifacts: [],
  events: [],
  latest_seq: 0,
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, (message: MessageEvent<string>) => void>();
  onerror: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, listener as (message: MessageEvent<string>) => void);
  }
  close() { this.closed = true; }
  emit(type: string, value: RuntimeEvent) {
    this.listeners.get(type)?.({ data: JSON.stringify(value) } as MessageEvent<string>);
  }
  fail() { this.onerror?.(); }
}

async function flush(): Promise<void> {
  await act(async () => {
    for (let index = 0; index < 8; index += 1) await Promise.resolve();
  });
}

describe("useSessionRuntime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("loads one snapshot and reconnects from paged incremental events", async () => {
    api.session.mockResolvedValue(snapshot());
    api.events
      .mockResolvedValueOnce({ events: [event(1)], latest_seq: 2 })
      .mockResolvedValueOnce({ events: [event(2)], latest_seq: 2 })
      .mockResolvedValueOnce({ events: [], latest_seq: 2 });

    const { result, unmount } = renderHook(() => useSessionRuntime("task"));
    await flush();

    expect(api.session).toHaveBeenCalledTimes(1);
    expect(api.events).toHaveBeenNthCalledWith(1, "task", 0);
    expect(api.events).toHaveBeenNthCalledWith(2, "task", 1);
    expect(FakeEventSource.instances[0].url).toContain("after_seq=2");
    expect(result.current.snapshot?.events.map((item) => item.seq)).toEqual([1, 2]);

    act(() => FakeEventSource.instances[0].fail());
    await act(async () => { vi.advanceTimersByTime(800); });
    await flush();

    expect(api.session).toHaveBeenCalledTimes(1);
    expect(api.events).toHaveBeenNthCalledWith(3, "task", 2);
    expect(FakeEventSource.instances[1].url).toContain("after_seq=2");

    act(() => FakeEventSource.instances[1].emit("event", event(2)));
    expect(result.current.snapshot?.events.map((item) => item.seq)).toEqual([1, 2]);
    unmount();
  });

  it("clears the previous task snapshot while the next task is loading", async () => {
    let resolveNext!: (value: RuntimeSnapshot) => void;
    api.session.mockResolvedValueOnce(snapshot("first")).mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve; }));
    api.events.mockResolvedValue({ events: [], latest_seq: 0 });
    const { result, rerender, unmount } = renderHook(({ taskId }) => useSessionRuntime(taskId), { initialProps: { taskId: "first" } });
    await flush();
    expect(result.current.snapshot?.task.id).toBe("first");

    rerender({ taskId: "second" });
    expect(result.current.snapshot).toBeNull();
    await act(async () => resolveNext(snapshot("second")));
    await flush();
    expect(result.current.snapshot?.task.id).toBe("second");
    unmount();
  });

  it("retries the bootstrap after an initial failure and then creates SSE", async () => {
    api.session.mockRejectedValueOnce(new Error("snapshot unavailable")).mockResolvedValueOnce(snapshot());
    api.events.mockResolvedValue({ events: [], latest_seq: 0 });
    const { result, unmount } = renderHook(() => useSessionRuntime("task"));
    await flush();
    expect(result.current.connection).toBe("offline");
    expect(FakeEventSource.instances).toHaveLength(0);

    act(() => result.current.refresh());
    await flush();
    expect(api.session).toHaveBeenCalledTimes(2);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(result.current.connection).toBe("live");
    unmount();
  });

  it("refreshes the authoritative snapshot after an incomplete strategy event", async () => {
    api.session.mockResolvedValueOnce(snapshot()).mockResolvedValueOnce({ ...snapshot(), latest_seq: 1 });
    api.events.mockResolvedValue({ events: [], latest_seq: 0 });
    const { unmount } = renderHook(() => useSessionRuntime("task"));
    await flush();
    act(() => FakeEventSource.instances[0].emit("event", { ...event(1), type: "STRATEGY_CARD_CREATED", payload: { strategy_card_id: "card" } }));
    await act(async () => { vi.advanceTimersByTime(40); });
    await flush();
    expect(api.session).toHaveBeenCalledTimes(2);
    unmount();
  });
});
