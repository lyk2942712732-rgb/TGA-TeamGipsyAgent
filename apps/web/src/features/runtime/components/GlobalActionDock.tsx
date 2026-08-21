import { useState } from "react";
import { CircleStop, MessageSquareText, ShieldCheck } from "lucide-react";
import { runtimeApi } from "../../../runtime/api-v2";
import { selectPendingApprovals } from "../models/selectors";
import type { RuntimeStore } from "../models/types";

/**
 * Mission Control's bottom dock: three task-level actions, each
 * carrying its own tone.  Every button is always drawn so the bar keeps its
 * shape; the ones the session state forbids are disabled rather than removed.
 */
export function GlobalActionDock({ store, mode, onRefresh, onOpenApprovals, onIntervention = () => undefined }: {
  store: RuntimeStore;
  mode: "runtime" | "replay";
  onRefresh: () => void;
  onOpenApprovals: () => void;
  onIntervention?: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const readonly = mode === "replay";
  const pending = selectPendingApprovals(store).length;
  const status = store.session.status;
  const finished = ["completed", "completed_with_limitations", "cancelled", "failed"].includes(status);

  const control = async (action: "cancel") => {
    setBusy(action); setMessage(null);
    try { const result = await runtimeApi.control(store.task.id, action); setMessage(result.accepted === false ? (result.reason ?? "当前 Runtime 不支持该控制操作") : "控制请求已提交"); onRefresh(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "控制请求失败"); }
    finally { setBusy(null); }
  };

  return <section className="global-action-dock" aria-label="全局操作">
    <button className="tone-violet" disabled={readonly} onClick={onIntervention}>
      <MessageSquareText size={15} aria-hidden="true" />{status === "awaiting_user_input" ? "回答 Supervisor" : "添加提示"}
    </button>
    <button className="tone-warn" onClick={onOpenApprovals}>
      <ShieldCheck size={15} aria-hidden="true" />审批中心{pending ? ` (${pending})` : ""}
    </button>
    <button className="danger" disabled={readonly || busy !== null || finished} onClick={() => void control("cancel")}>
      <CircleStop size={15} aria-hidden="true" />取消任务
    </button>
    {message ? <span className="dock-message" role="status">{message}</span> : null}
  </section>;
}
