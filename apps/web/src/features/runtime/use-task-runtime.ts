import { useCallback, useEffect, useRef, useState } from "react";
import { runtimeApi } from "../../runtime/api-v2";
import { normalizeRuntimeEvent } from "./models/normalize";
import { mergeRuntimeEvents } from "./models/reducer";
import type { RuntimeEvent, RuntimeStore } from "./models/types";

export type RuntimeConnection = "loading" | "live" | "reconnecting" | "offline";

export function useTaskRuntime(taskId: string | null, options: { live?: boolean } = {}) {
  const live = options.live ?? true;
  const [store, setStore] = useState<RuntimeStore | null>(null);
  const [storeTaskId, setStoreTaskId] = useState<string | null>(null);
  const [connection, setConnection] = useState<RuntimeConnection>("loading");
  const [error, setError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const storeRef = useRef<RuntimeStore | null>(null);
  const cursor = useRef(0);
  const sourceRef = useRef<EventSource | null>(null);
  const refresh = useCallback(() => { setConnection("loading"); setRetryNonce((value) => value + 1); }, []);

  useEffect(() => {
    sourceRef.current?.close(); sourceRef.current = null;
    storeRef.current = null; cursor.current = 0;
    setStore(null); setStoreTaskId(null); setError(null);
    if (!taskId) { setConnection("offline"); return; }
    setConnection("loading");

    let active = true;
    let reconnectAttempt = 0;
    let reconnectTimer: number | null = null;
    let refreshTimer: number | null = null;
    let eventBatchTimer: number | null = null;
    let eventBatch: RuntimeEvent[] = [];
    let refreshInFlight = false;
    const close = () => { sourceRef.current?.close(); sourceRef.current = null; };
    const backoff = () => Math.min(10_000, 800 * 2 ** Math.min(reconnectAttempt++, 4));
    const publish = (next: RuntimeStore) => { storeRef.current = next; cursor.current = next.latestSeq; setStore(next); setStoreTaskId(taskId); };

    const authoritativeRefresh = async () => {
      if (refreshInFlight) return;
      refreshInFlight = true;
      try {
        let next = await runtimeApi.taskRuntime(taskId);
        if (!active) return;
        const buffered = storeRef.current
          ? Object.values(storeRef.current.eventsBySeq).filter((event) => event.seq > next.latestSeq)
          : [];
        const merged = mergeRuntimeEvents(next, buffered);
        if (!merged.gap) next = merged.state;
        publish(next); setError(null);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "无法刷新任务快照");
      } finally { refreshInFlight = false; }
    };
    const scheduleRefresh = () => {
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(() => { refreshTimer = null; void authoritativeRefresh(); }, 40);
    };
    const applyEvents = (events: RuntimeEvent[]): boolean => {
      const current = storeRef.current;
      if (!current || !events.length) return true;
      const result = mergeRuntimeEvents(current, events);
      if (result.gap) return false;
      publish(result.state);
      if (result.needsRefresh) scheduleRefresh();
      return true;
    };
    const flushEventBatch = () => {
      if (eventBatchTimer !== null) window.clearTimeout(eventBatchTimer);
      eventBatchTimer = null;
      const pending = eventBatch;
      eventBatch = [];
      if (pending.length) applyEvents(pending);
    };
    const enqueueEvent = (event: RuntimeEvent) => {
      eventBatch.push(event);
      if (eventBatchTimer === null) eventBatchTimer = window.setTimeout(flushEventBatch, 16);
    };
    const fillGap = async (pending?: RuntimeEvent) => {
      flushEventBatch();
      let serverLatest = cursor.current;
      do {
        const missed = await runtimeApi.runtimeEvents(taskId, cursor.current);
        if (!active) return;
        serverLatest = missed.latestSeq;
        if (!missed.events.length) break;
        if (!applyEvents(missed.events)) { await authoritativeRefresh(); break; }
      } while (cursor.current < serverLatest);
      if (pending && pending.seq > cursor.current) {
        if (!applyEvents([pending])) await authoritativeRefresh();
      }
      setError(null);
    };
    const connect = async () => {
      try {
        await fillGap();
        if (!active) return;
        const source = new EventSource(runtimeApi.streamUrl(taskId, cursor.current));
        sourceRef.current = source;
        source.addEventListener("event", (message) => {
          let event: RuntimeEvent;
          try { event = normalizeRuntimeEvent(JSON.parse((message as MessageEvent<string>).data)); }
          catch { setError("收到无法解析的运行时事件；连接保持开启并等待服务端补偿"); return; }
          const queuedCursor = eventBatch[eventBatch.length - 1]?.seq ?? cursor.current;
          if (event.seq <= queuedCursor) return;
          if (event.seq > queuedCursor + 1) {
            flushEventBatch();
            void fillGap(event).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "事件序列补偿失败"));
            return;
          }
          enqueueEvent(event); setConnection("live"); reconnectAttempt = 0;
        });
        source.addEventListener("heartbeat", () => { setConnection("live"); reconnectAttempt = 0; });
        source.onerror = () => {
          close(); if (!active) return;
          setConnection("reconnecting"); reconnectTimer = window.setTimeout(() => void connect(), backoff());
        };
        setConnection("live");
      } catch (reason) {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "无法连接实时事件流");
        setConnection("reconnecting"); reconnectTimer = window.setTimeout(() => void connect(), backoff());
      }
    };
    const bootstrap = async () => {
      try {
        const initial = await runtimeApi.taskRuntime(taskId);
        if (!active) return;
        publish(initial); setError(null);
        if (live) await connect(); else setConnection("offline");
      } catch (reason) {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "无法加载任务快照"); setConnection("offline");
      }
    };
    void bootstrap();
    return () => {
      active = false; close();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
      if (eventBatchTimer !== null) window.clearTimeout(eventBatchTimer);
    };
  }, [taskId, retryNonce, live]);

  return { store: storeTaskId === taskId ? store : null, connection, error, refresh };
}
