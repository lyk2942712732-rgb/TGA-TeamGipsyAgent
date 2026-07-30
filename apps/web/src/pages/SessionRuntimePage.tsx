import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Activity, Check, Clock3, Cpu, FileText, Pause, Play, Radio, ShieldAlert, Square, X, Zap } from "lucide-react";
import { runtimeApi } from "../api/runtime";
import { ReactWorkbench, RuntimeLoading, redact } from "../components/runtime/ReactWorkbench";
import { useSessionRuntime } from "../runtime/session-store";
import type { RuntimeSnapshot } from "../runtime/event-types";
import { MODE_PROFILES } from "../modes";

type Props = { taskId: string; mode: "runtime" | "replay"; onReplay: () => void };
const statusLabels: Record<string, string> = { created: "已创建", running: "运行中", paused: "已暂停", awaiting_approval: "等待审批", completed: "已完成", blocked: "已阻塞", cancelled: "已取消", failed: "执行失败" };

export function SessionRuntimePage({ taskId, mode, onReplay }: Props) {
  const { snapshot, connection, error, refresh } = useSessionRuntime(taskId);
  const [busy, setBusy] = useState<"pause" | "resume" | "cancel" | "hint" | "approval" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [hintOpen, setHintOpen] = useState(false);
  const [hint, setHint] = useState("");
  const usage = useMemo(() => snapshot ? snapshot.events.reduce((total, event) => total + Number(event.payload.input_tokens ?? 0) + Number(event.payload.output_tokens ?? 0), 0) : 0, [snapshot]);

  useEffect(() => { setBusy(null); setNotice(null); setConfirmCancel(false); setHintOpen(false); setHint(""); }, [taskId]);

  if (!snapshot) return <section className="runtime-workspace"><RuntimeLoading error={error} onRetry={() => void refresh()} /></section>;

  const profile = MODE_PROFILES[snapshot.task.mode];
  const model = snapshot.task.model_snapshot?.model || snapshot.solvers.find((solver) => solver.id === snapshot.session.active_solver_id)?.model_name || "未记录模型";
  const elapsed = elapsedLabel(snapshot.session.started_at, snapshot.session.finished_at);
  const runtimeError = latestRuntimeError(snapshot);
  const pendingApproval = snapshot.session.status === "awaiting_approval"
    ? snapshot.actions.find((action) => action.status === "pending_approval")
    : undefined;
  const control = async (action: "pause" | "resume" | "cancel") => {
    setBusy(action); setNotice(null);
    try {
      const result = await runtimeApi.control(taskId, action);
      if (result.accepted === false) throw new Error(result.reason || "运行时拒绝了控制请求");
      setNotice(action === "pause" ? "暂停请求已接受，将在当前边界停止下一回合。" : action === "resume" ? "恢复请求已接受，将从已保存的对话记录与事件位置继续。" : "任务已取消。");
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "运行时控制失败");
    } finally { setBusy(null); setConfirmCancel(false); }
  };
  const decideApproval = async (actionId: string, decision: "approve_action" | "reject_action") => {
    setBusy("approval"); setNotice(null);
    try {
      const result = await runtimeApi.control(taskId, decision, actionId);
      if (result.accepted === false) throw new Error(result.reason || "运行时拒绝了审批决定");
      setNotice(decision === "approve_action" ? "已批准该操作，将执行已保存的原始动作。" : "已拒绝该操作，拒绝结果将返回给当前模型对话。");
      void refresh();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "审批操作失败");
    } finally { setBusy(null); }
  };
  const submitHint = async (event: FormEvent) => {
    event.preventDefault(); if (!hint.trim()) return;
    setBusy("hint"); setNotice(null);
    try { const result = await runtimeApi.hint(taskId, hint.trim()); if (result.accepted === false) throw new Error(result.reason || "运行时拒绝了提示"); setHint(""); setHintOpen(false); setNotice("提示已写入证据记忆，并关联新的候选策略。" ); }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "提示提交失败"); }
    finally { setBusy(null); }
  };

  return <section className={`runtime-workspace react-runtime-page ${mode === "replay" ? "is-replay" : ""}`}>
    <header className="react-session-header">
      <div className="session-heading">
        <div className="session-kicker"><Activity size={14} /><span>{profile.label}</span><i>/</i><code>{snapshot.task.id}</code></div>
        <div><h1>{snapshot.task.name}</h1><span className={`session-state ${snapshot.session.status}`} data-testid="session-status"><i />{mode === "replay" ? "只读回放" : statusLabels[snapshot.session.status] ?? snapshot.session.status}</span></div>
        <p title={snapshot.task.task_entry_url || snapshot.task.prompt}>{snapshot.task.task_entry_url || snapshot.task.prompt || snapshot.task.goal || "本地输入任务"}</p>
      </div>
      <div className="session-telemetry">
        <Metric icon={<Cpu size={14} />} label="模型" value={model} />
        <Metric icon={<Zap size={14} />} label="回合" value={`${snapshot.session.turn_count}/${snapshot.session.max_turns}`} />
        <Metric icon={<FileText size={14} />} label="Token" value={usage.toLocaleString()} />
        <Metric icon={<Clock3 size={14} />} label="运行时间" value={elapsed} />
        <Metric icon={<Radio size={14} />} label="事件流" value={connectionLabel(connection)} tone={connection} />
      </div>
      <div className="session-controls">
        <a href={runtimeApi.reportUrl(taskId)} target="_blank" rel="noreferrer">报告</a>
        {mode === "runtime" ? <button onClick={onReplay}>回放</button> : null}
        {mode === "runtime" ? <button onClick={() => setHintOpen(true)}>补充提示</button> : null}
        {mode === "runtime" && snapshot.session.status === "running" ? <button disabled={busy !== null} onClick={() => void control("pause")}><Pause size={13} />暂停</button> : null}
        {mode === "runtime" && ["paused", "blocked"].includes(snapshot.session.status) ? <button className="primary" disabled={busy !== null} onClick={() => void control("resume")}><Play size={13} />恢复</button> : null}
        {mode === "runtime" && !["completed", "cancelled", "failed"].includes(snapshot.session.status) ? <button className="danger" disabled={busy !== null} onClick={() => setConfirmCancel(true)}><Square size={12} />取消</button> : null}
      </div>
      {snapshot.session.stop_reason ? <div className="stop-reason"><b>stop_reason</b><code>{snapshot.session.stop_reason}</code></div> : null}
    </header>

    {mode === "replay" ? <div className="runtime-banner">回放模式只读取已保存的运行快照和事件，不发送控制、提示或目标请求。</div> : null}
    {error ? <div className="runtime-banner error" role="alert">实时同步降级：{error}<button onClick={() => void refresh()}>重试</button></div> : null}
    {notice ? <div className="runtime-banner" role="status">{notice}<button onClick={() => setNotice(null)}>关闭</button></div> : null}
    {runtimeError ? <RuntimeErrorPanel error={runtimeError} retrying={busy === "resume"} onRetry={snapshot.session.status === "blocked" ? () => void control("resume") : undefined} /> : null}
    {mode === "runtime" && pendingApproval ? <ApprovalCard action={pendingApproval} busy={busy === "approval"} onDecide={decideApproval} /> : null}
    <ReactWorkbench snapshot={snapshot} />

    {hintOpen ? <div className="runtime-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setHintOpen(false); }}><form className="runtime-modal" role="dialog" aria-modal="true" aria-labelledby="hint-title" onSubmit={submitHint}><header><div><span>人工补充</span><h2 id="hint-title">补充任务上下文</h2></div><button type="button" onClick={() => setHintOpen(false)}>关闭</button></header><p>提示将作为未经验证的候选指导写入证据记忆，不会扩大授权范围，也不会直接成为已验证结论。</p><label>补充提示<textarea autoFocus maxLength={800} value={hint} onChange={(event) => setHint(event.target.value)} placeholder="补充已知路径、失败边界或验证建议" /><small>{hint.length}/800</small></label><footer><button type="button" onClick={() => setHintOpen(false)}>返回</button><button className="primary" disabled={busy === "hint" || !hint.trim()}>{busy === "hint" ? "提交中" : "提交提示"}</button></footer></form></div> : null}
    {confirmCancel ? <div className="runtime-modal-backdrop"><section className="runtime-modal" role="dialog" aria-modal="true" aria-labelledby="cancel-title"><header><div><span>任务控制</span><h2 id="cancel-title">取消这个任务？</h2></div></header><p>当前执行链路会停止，并保存已经完成的操作、证据产物和运行事件。该操作不会删除任务文件。</p><footer><button onClick={() => setConfirmCancel(false)}>返回</button><button className="danger" disabled={busy === "cancel"} onClick={() => void control("cancel")}>{busy === "cancel" ? "正在取消" : "确认取消"}</button></footer></section></div> : null}
  </section>;
}

function ApprovalCard({ action, busy, onDecide }: { action: RuntimeSnapshot["actions"][number]; busy: boolean; onDecide: (actionId: string, decision: "approve_action" | "reject_action") => void }) {
  const effect = action.effect;
  return <section className="approval-card" aria-label="高影响操作审批">
    <header><div><span>高影响操作审查</span><h2><ShieldAlert size={17} />需要审批的操作</h2></div><b>{approvalDeadline(action.approval_expires_at)}</b></header>
    <dl>
      <div><dt>能力</dt><dd>{action.capability}</dd></div><div><dt>目标</dt><dd>{action.actual_target || action.target}</dd></div>
      <div><dt>风险</dt><dd>{riskLabel(action.risk)}</dd></div><div><dt>预期结果</dt><dd>{action.expected_outcome || "未声明"}</dd></div>
      <div><dt>副作用</dt><dd>{effect ? `${effectCategory(effect.category)} · ${effectScope(effect.scope)} · ${effectPersistence(effect.persistence)}` : "未声明"}</dd></div><div><dt>可逆性</dt><dd>{effect ? effectReversibility(effect.reversibility) : "未声明"}</dd></div>
    </dl>
    {effect?.description ? <p><small>副作用说明</small>{effect.description}</p> : null}
    {action.alternative_analysis ? <p><small>替代方案</small>{action.alternative_analysis}</p> : null}
    {action.arguments && Object.keys(action.arguments).length ? <p><small>脱敏参数摘要</small><code>{approvalArguments(action.arguments)}</code></p> : null}
    <footer><button className="danger" disabled={busy} onClick={() => onDecide(action.id, "reject_action")}><X size={13} />拒绝</button><button className="primary" disabled={busy} onClick={() => onDecide(action.id, "approve_action")}><Check size={13} />批准并执行</button></footer>
  </section>;
}

type RuntimeErrorView = { phase: string; code: string; message: string; retryable: boolean; suggestion: string };
function RuntimeErrorPanel({ error, retrying, onRetry }: { error: RuntimeErrorView; retrying: boolean; onRetry?: () => void }) {
  return <section className="runtime-error-panel" role="alert" aria-label="运行时错误">
    <header><div><span>RUNTIME ERROR</span><h2><ShieldAlert size={17} />{errorPhase(error.phase)}</h2></div><b>{error.retryable ? "可以重试" : "不可重试"}</b></header>
    <dl><div><dt>错误代码</dt><dd><code>{error.code}</code></dd></div><div><dt>错误信息</dt><dd>{error.message}</dd></div><div><dt>建议操作</dt><dd>{error.suggestion}</dd></div></dl>
    {onRetry && error.retryable ? <footer><button className="primary" disabled={retrying} onClick={onRetry}><Play size={13} />{retrying ? "正在恢复" : "恢复会话"}</button></footer> : null}
  </section>;
}

function Metric({ icon, label, value, tone = "" }: { icon: React.ReactNode; label: string; value: string; tone?: string }) { return <span className={tone}>{icon}<small>{label}</small><b title={value}>{value}</b></span>; }
function connectionLabel(value: string) { return value === "live" ? "实时" : value === "reconnecting" ? "重连中" : value === "loading" ? "连接中" : "离线"; }
function elapsedLabel(start?: string | null, finish?: string | null) { if (!start) return "-"; const from = new Date(start).getTime(); const to = finish ? new Date(finish).getTime() : Date.now(); if (!Number.isFinite(from) || !Number.isFinite(to)) return "-"; const seconds = Math.max(0, Math.round((to - from) / 1000)); return seconds >= 3600 ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m` : seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`; }
function riskLabel(value?: string) { return value === "destructive" ? "破坏性" : value === "active" ? "主动交互" : value === "passive" ? "被动观察" : "未声明"; }
function effectCategory(value: string) { return ({ authentication: "认证", submission: "提交", file_write: "文件写入", resource_create: "创建资源", resource_modify: "修改资源", resource_delete: "删除资源", containment: "隔离处置", destructive_scan: "破坏性扫描" } as Record<string, string>)[value] ?? value; }
function effectScope(value: string) { return ({ none: "无外部影响", session: "当前会话", workspace: "工作区", target: "目标系统" } as Record<string, string>)[value] ?? value; }
function effectPersistence(value: string) { return ({ none: "不持久化", temporary: "临时", persistent: "持久" } as Record<string, string>)[value] ?? value; }
function effectReversibility(value: string) { return ({ not_applicable: "不适用", reversible: "可恢复", uncertain: "恢复性未知", irreversible: "不可恢复" } as Record<string, string>)[value] ?? value; }
function approvalDeadline(value?: string | null) { if (!value) return "未设置截止时间"; const time = new Date(value).getTime(); if (!Number.isFinite(time)) return "截止时间无效"; const seconds = Math.max(0, Math.ceil((time - Date.now()) / 1000)); return seconds === 0 ? "审批已到期" : `剩余 ${seconds >= 60 ? `${Math.ceil(seconds / 60)} 分钟` : `${seconds} 秒`}`; }
function approvalArguments(value: Record<string, unknown>) { const encoded = JSON.stringify(value); return encoded.length > 800 ? `${encoded.slice(0, 800)}...` : encoded; }
function latestRuntimeError(snapshot: RuntimeSnapshot): RuntimeErrorView | null {
  if (!["blocked", "failed"].includes(snapshot.session.status)) return null;
  for (const event of [...snapshot.events].reverse()) {
    const payloadError = event.payload.error;
    if (payloadError?.code || event.payload.code || ["AGENT_ERROR", "RUNTIME_ERROR"].includes(event.type)) {
      const code = payloadError?.code || event.payload.code || (event.payload.phase === "model_turn" ? "MODEL_REQUEST_FAILED" : "RUNTIME_ERROR");
      const phase = payloadError?.phase || event.payload.phase || String(event.payload.phase || "runtime");
      const message = redact(payloadError?.message || event.payload.message || String(snapshot.session.stop_reason || "运行时发生错误"));
      const retryable = payloadError?.retryable ?? event.payload.retryable ?? snapshot.session.status === "blocked";
      return { code, phase, message, retryable, suggestion: errorSuggestion(code, retryable) };
    }
  }
  if (!snapshot.session.stop_reason) return null;
  const code = snapshot.session.stop_reason === "model_request_failed" ? "MODEL_REQUEST_FAILED" : snapshot.session.stop_reason.toUpperCase();
  const retryable = snapshot.session.status === "blocked";
  return { code, phase: "runtime", message: redact(snapshot.session.stop_reason), retryable, suggestion: errorSuggestion(code, retryable) };
}
function errorPhase(value: string) { return ({ provider: "模型请求失败", model_turn: "模型请求失败", http: "网络请求失败", process: "隔离计算失败", mcp: "MCP 执行失败", policy: "策略拒绝", approval: "审批失败", completion: "完成校验失败", runtime: "运行时错误" } as Record<string, string>)[value] ?? "运行时错误"; }
function errorSuggestion(code: string, retryable: boolean) { if (code.includes("MODEL")) return "检查 Provider 状态、输出 Token 和最近验证结果，然后恢复会话。"; if (code.includes("CREDENTIAL")) return "重新配置并验证 Provider 凭据。"; if (code.includes("NETWORK") || code.includes("HTTP")) return "检查任务网络边界和目标可达性后重试。"; if (code.includes("ISOLATED")) return "确认 Docker 隔离运行时和镜像可用。"; return retryable ? "确认错误原因已消除后恢复会话。" : "查看事件和证据记录，修正任务配置后创建新任务。"; }
export { redact } from "../components/runtime/ReactWorkbench";
