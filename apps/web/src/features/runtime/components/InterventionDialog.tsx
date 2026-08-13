import { useEffect, useState, type FormEvent } from "react";
import { runtimeApi } from "../../../runtime/api-v2";
import { selectSupervisor } from "../models/selectors";
import type { RuntimeStore } from "../models/types";

type UIScope = "task" | "supervisor" | "solver" | "intent";
type Kind = "hint" | "instruction" | "constraint" | "priority_change" | "answer";

export function InterventionDialog({ store, open, onClose, onSubmitted }: { store: RuntimeStore; open: boolean; onClose: () => void; onSubmitted: () => void }) {
  const [scope, setScope] = useState<UIScope>("task");
  const [kind, setKind] = useState<Kind>("hint");
  const [targetId, setTargetId] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (!open) { setScope("task"); setKind("hint"); setTargetId(""); setContent(""); setError(null); } }, [open]);
  if (!open) return null;
  const answering = store.session.status === "awaiting_user_input";
  const request = store.session.userInputRequest;
  const supervisorId = selectSupervisor(store)?.solverId ?? "";
  const actualTarget = scope === "supervisor" ? supervisorId : targetId;
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!content.trim() || (scope !== "task" && !actualTarget)) return;
    setBusy(true); setError(null);
    try {
      if (answering) await runtimeApi.userInput(store.task.id, content.trim());
      else await runtimeApi.intervention(store.task.id, { kind, content: content.trim(), scope: scope === "supervisor" ? "solver" : scope, ...(scope === "task" ? {} : { target_id: actualTarget }) });
      onSubmitted(); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Intervention 提交失败"); }
    finally { setBusy(false); }
  };
  return <div className="runtime-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }} onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}><form className="intervention-dialog" role="dialog" aria-modal="true" aria-labelledby="intervention-title" onSubmit={submit}><header><div><span>{answering ? "SUPERVISOR QUESTION" : "USER INTERVENTION"}</span><h2 id="intervention-title">{answering ? "回答 Supervisor" : "补充任务信息"}</h2></div><button type="button" onClick={onClose}>关闭</button></header>{answering ? <section className="supervisor-question"><b>Supervisor 正在等待你的回答</b><p>{request?.question ?? "Supervisor 需要额外信息后才能继续。"}</p>{request?.reason ? <small>原因：{request.reason}</small> : null}</section> : <><label>作用域<select value={scope} onChange={(event) => { setScope(event.target.value as UIScope); setTargetId(""); }}><option value="task">Task</option><option value="supervisor">Supervisor</option><option value="solver">Solver</option><option value="intent">Intent</option></select></label>{scope === "supervisor" ? <p>目标 Supervisor：<code>{supervisorId || "未实例化"}</code></p> : null}{scope === "solver" ? <label>目标 Solver<select value={targetId} onChange={(event) => setTargetId(event.target.value)} required><option value="">请选择</option>{Object.values(store.solversById).map((solver) => <option key={solver.solverId} value={solver.solverId}>{solver.solverId}</option>)}</select></label> : null}{scope === "intent" ? <label>目标 Intent<select value={targetId} onChange={(event) => setTargetId(event.target.value)} required><option value="">请选择</option>{Object.values(store.intentsById).map((intent) => <option key={intent.intentId} value={intent.intentId}>{intent.title}</option>)}</select></label> : null}<label>类型<select value={kind} onChange={(event) => setKind(event.target.value as Kind)}><option value="hint">Hint</option><option value="instruction">Instruction</option><option value="constraint">Constraint</option><option value="priority_change">Priority change</option><option value="answer">Answer</option></select></label></>}<label>{answering ? "你的回答" : "内容"}<textarea autoFocus maxLength={8000} placeholder={answering ? "针对上面的问题直接回答；回答不会自动扩大任务授权。" : undefined} value={content} onChange={(event) => setContent(event.target.value)} /></label><aside className="intervention-boundary">{answering ? <p>回答将恢复当前 LangGraph 检查点，并作为已记录的用户输入交给 Supervisor 重新决策。</p> : <><p>Hint 是尚未验证的线索；Constraint 是权威约束，但不会扩大 ExecutionPolicy。</p><p>指向 Solver 或 Intent 的内容不会默认广播给所有 Solver，也不会授予新的工具权限。</p></>}</aside>{error ? <p role="alert">{error}</p> : null}<footer><button type="button" onClick={onClose}>取消</button><button className="primary" disabled={busy || !content.trim() || (!answering && scope !== "task" && !actualTarget)}>{answering ? "提交回答并继续" : "提交 Intervention"}</button></footer></form></div>;
}
