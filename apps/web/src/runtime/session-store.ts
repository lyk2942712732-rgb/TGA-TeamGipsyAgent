import { useCallback, useEffect, useRef, useState } from "react";
import { runtimeApi } from "./api-v2";
import { applyRuntimeEvent, mergeEvents } from "./event-reducer";
import type { RuntimeEvent, RuntimeSnapshot } from "./event-types";

export function useSessionRuntime(taskId: string | null) {
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [connection, setConnection] = useState<"loading" | "live" | "reconnecting" | "offline">("loading");
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef(0); const sourceRef = useRef<EventSource | null>(null); const reconcileTimer = useRef<number | null>(null);
  const refresh = useCallback(async () => {
    if (!taskId) return;
    const next = await runtimeApi.session(taskId);
    cursor.current = next.latest_seq; setSnapshot(next); setError(null);
  }, [taskId]);
  const reconcile = useCallback(() => {
    if (reconcileTimer.current) window.clearTimeout(reconcileTimer.current);
    reconcileTimer.current = window.setTimeout(() => { void refresh().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法同步会话状态")); }, 300);
  }, [refresh]);

  useEffect(() => {
    if (!taskId) { setSnapshot(null); setConnection("offline"); return; }
    let live = true; let retry = 0; let timer: number | null = null;
    const close = () => { sourceRef.current?.close(); sourceRef.current = null; };
    const backoff = () => Math.min(10_000, 800 * 2 ** Math.min(retry++, 4));
    const fillGap = async (event: RuntimeEvent) => {
      try {
        const missed = await runtimeApi.events(taskId, cursor.current);
        if (!live) return;
        setSnapshot((current) => current ? mergeEvents(current, missed.events) : current);
        cursor.current = Math.max(cursor.current, missed.latest_seq, event.seq);
        reconcile();
      } catch (reason) { setError(reason instanceof Error ? reason.message : "事件序列补偿失败"); }
    };
    const connect = async () => {
      try {
        await refresh(); if (!live) return;
        const source = new EventSource(runtimeApi.streamUrl(taskId, cursor.current)); sourceRef.current = source;
        source.addEventListener("event", (message) => {
          const event = JSON.parse((message as MessageEvent<string>).data) as RuntimeEvent;
          if (event.seq <= cursor.current) return;
          if (event.seq > cursor.current + 1) { void fillGap(event); return; }
          cursor.current = event.seq; setSnapshot((current) => current ? applyRuntimeEvent(current, event) : current); setConnection("live"); retry = 0; reconcile();
        });
        source.addEventListener("heartbeat", () => { setConnection("live"); retry = 0; });
        source.onerror = () => { close(); if (!live) return; setConnection("reconnecting"); timer = window.setTimeout(connect, backoff()); };
        setConnection("live");
      } catch (reason) { if (live) { setError(reason instanceof Error ? reason.message : "无法连接实时事件流"); setConnection("reconnecting"); timer = window.setTimeout(connect, backoff()); } }
    };
    void connect();
    return () => { live = false; close(); if (timer) window.clearTimeout(timer); if (reconcileTimer.current) window.clearTimeout(reconcileTimer.current); };
  }, [reconcile, refresh, taskId]);
  return { snapshot, connection, error, refresh };
}
