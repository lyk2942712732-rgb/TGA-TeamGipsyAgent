import { useState } from "react";
import { runtimeApi } from "../../../runtime/api-v2";
import { selectPendingApprovals } from "../models/selectors";
import type { RuntimeStore } from "../models/types";

export function GlobalActionDock({ store, mode, onRefresh, onOpenApprovals }: { store: RuntimeStore; mode: "runtime" | "replay"; onRefresh: () => void; onOpenApprovals: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const readonly = mode === "replay";
  const pending = selectPendingApprovals(store).length;
  const control = async (action: "pause" | "resume" | "cancel") => {
    setBusy(action); setMessage(null);
    try { await runtimeApi.control(store.task.id, action); setMessage("控制请求已提交"); onRefresh(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "控制请求失败"); }
    finally { setBusy(null); }
  };
  return <section className="global-action-dock" aria-label="全局操作">
    <div><b>{readonly ? "只读回放" : "Task 控制"}</b><small>{pending ? `${pending} 项待审批` : "没有待审批操作"}</small></div>
    {message ? <span role="status">{message}</span> : null}
    <button onClick={onOpenApprovals}>审批队列 {pending ? `(${pending})` : ""}</button>
    {!readonly && store.session.status === "running" ? <button disabled={busy !== null} onClick={() => void control("pause")}>暂停 Task</button> : null}
    {!readonly && ["paused", "blocked"].includes(store.session.status) ? <button disabled={busy !== null} onClick={() => void control("resume")}>恢复 Task</button> : null}
    {!readonly && !["completed", "cancelled", "failed"].includes(store.session.status) ? <button className="danger" disabled={busy !== null} onClick={() => void control("cancel")}>取消 Task</button> : null}
  </section>;
}
