import { useCallback, useEffect, useRef, useState } from "react";
import { AgentEventSchema } from "../api/schemas";
import { runtimeApi } from "./api-v2";
import { applyRuntimeEvent, mergeEvents, runtimeEventNeedsSnapshot } from "./event-reducer";
import type { RuntimeEvent, RuntimeSnapshot } from "./event-types";

export function useSessionRuntime(taskId: string | null) {
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [snapshotTaskId, setSnapshotTaskId] = useState<string | null>(null);
  const [connection, setConnection] = useState<"loading" | "live" | "reconnecting" | "offline">("loading");
  const [error, setError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const cursor = useRef(0);
  const sourceRef = useRef<EventSource | null>(null);
  const retry = useCallback(() => { setConnection("loading"); setRetryNonce((value) => value + 1); }, []);

  useEffect(() => {
    sourceRef.current?.close(); sourceRef.current = null;
    cursor.current = 0;
    setSnapshot(null); setSnapshotTaskId(null); setError(null);
    if (!taskId) { setConnection("offline"); return; }
    setConnection("loading");

    let active = true;
    let reconnectAttempt = 0;
    let reconnectTimer: number | null = null;
    let refreshTimer: number | null = null;
    let refreshInFlight = false;
    const close = () => { sourceRef.current?.close(); sourceRef.current = null; };
    const backoff = () => Math.min(10_000, 800 * 2 ** Math.min(reconnectAttempt++, 4));

    const authoritativeRefresh = async () => {
      if (refreshInFlight) return;
      refreshInFlight = true;
      try {
        const next = await runtimeApi.session(taskId);
        if (!active) return;
        cursor.current = Math.max(cursor.current, next.latest_seq);
        setSnapshot((current) => current ? mergeEvents(next, current.events.filter((event) => event.seq > next.latest_seq)) : next);
        setSnapshotTaskId(taskId); setError(null);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "无法刷新 Session Snapshot");
      } finally { refreshInFlight = false; }
    };
    const scheduleRefresh = () => {
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(() => { refreshTimer = null; void authoritativeRefresh(); }, 40);
    };
    const applyEvents = (events: RuntimeEvent[]) => {
      if (!events.length) return;
      setSnapshot((current) => current ? mergeEvents(current, events) : current);
      cursor.current = Math.max(cursor.current, events[events.length - 1].seq);
      if (events.some(runtimeEventNeedsSnapshot)) scheduleRefresh();
    };
    const fillGap = async (pending?: RuntimeEvent) => {
      let serverLatest = cursor.current;
      do {
        const missed = await runtimeApi.events(taskId, cursor.current);
        if (!active) return;
        serverLatest = missed.latest_seq;
        if (!missed.events.length) break;
        applyEvents(missed.events);
      } while (cursor.current < serverLatest);
      if (pending && pending.seq > cursor.current) applyEvents([pending]);
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
          try { event = AgentEventSchema.parse(JSON.parse((message as MessageEvent<string>).data)) as RuntimeEvent; }
          catch { setError("收到无法解析的运行时事件，已保留连接等待服务端补偿"); return; }
          if (event.seq <= cursor.current) return;
          if (event.seq > cursor.current + 1) { void fillGap(event).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "事件序列补偿失败")); return; }
          applyEvents([event]); setConnection("live"); reconnectAttempt = 0;
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
        const next = await runtimeApi.session(taskId);
        if (!active) return;
        cursor.current = next.latest_seq; setSnapshot(next); setSnapshotTaskId(taskId); setError(null);
        await connect();
      } catch (reason) {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "无法加载初始 Session Snapshot"); setConnection("offline");
      }
    };
    void bootstrap();
    return () => { active = false; close(); if (reconnectTimer !== null) window.clearTimeout(reconnectTimer); if (refreshTimer !== null) window.clearTimeout(refreshTimer); };
  }, [taskId, retryNonce]);

  return { snapshot: snapshotTaskId === taskId ? snapshot : null, connection, error, refresh: retry };
}
