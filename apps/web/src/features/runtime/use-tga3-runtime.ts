import { useCallback, useEffect, useState } from "react";
import { loadTGA3Snapshot, type TGA3RuntimeSnapshot } from "../../runtime/tga3-runtime";

export type TGA3Connection = "loading" | "live" | "reconnecting" | "offline";

export function useTGA3Runtime(taskId: string | null) {
  const [snapshot, setSnapshot] = useState<TGA3RuntimeSnapshot | null>(null);
  const [connection, setConnection] = useState<TGA3Connection>("loading");
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((value) => value + 1), []);
  useEffect(() => {
    if (!taskId) { setSnapshot(null); setConnection("offline"); return; }
    let active = true;
    let timer: number | undefined;
    const load = async () => {
      try {
        const next = await loadTGA3Snapshot(taskId);
        if (!active) return;
        setSnapshot(next); setError(null);
        const terminal = ["completed", "failed", "cancelled", "stopped"].includes(next.task.state);
        setConnection(terminal ? "offline" : "live");
        if (!terminal) timer = window.setTimeout(() => void load(), 1500);
      } catch (reason) {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "无法加载 TGA3 任务状态");
        setConnection("reconnecting");
        timer = window.setTimeout(() => void load(), 3000);
      }
    };
    setConnection("loading"); void load();
    return () => { active = false; if (timer) window.clearTimeout(timer); };
  }, [taskId, nonce]);
  return { snapshot, connection, error, refresh };
}
