import { useCallback, useEffect, useState } from "react";
import { runtimeApi } from "../../runtime/api-v2";
import type { RuntimeStore } from "./models/types";

export type RuntimeConnection = "loading" | "live" | "reconnecting" | "offline";

/** TGA3 projects blackboard, dialogue and agent state as one snapshot. */
export function useTaskRuntime(taskId: string | null, options: { live?: boolean } = {}) {
  const live = options.live ?? true;
  const [store, setStore] = useState<RuntimeStore | null>(null);
  const [connection, setConnection] = useState<RuntimeConnection>("loading");
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    if (!taskId) { setStore(null); setConnection("offline"); return; }
    let active = true;
    let timer: number | undefined;
    const load = async () => {
      try {
        const next = await runtimeApi.taskRuntime(taskId);
        if (!active) return;
        setStore(next); setError(null);
        const terminal = ["completed", "failed", "cancelled"].includes(next.session.status);
        setConnection(live && !terminal ? "live" : "offline");
        if (live && !terminal) timer = window.setTimeout(() => void load(), 1500);
      } catch (reason) {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "无法加载 TGA3 运行状态");
        setConnection("reconnecting");
        if (live) timer = window.setTimeout(() => void load(), 3000);
      }
    };
    setConnection("loading"); void load();
    return () => { active = false; if (timer) window.clearTimeout(timer); };
  }, [taskId, live, nonce]);

  return { store, connection, error, refresh };
}
