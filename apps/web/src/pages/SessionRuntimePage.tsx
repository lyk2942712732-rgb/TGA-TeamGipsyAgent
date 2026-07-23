import { useState, type FormEvent } from "react";
import { runtimeApi } from "../api/runtime";
import { RuntimeLoading } from "../components/runtime/RuntimePanels";
import { AttackFlow } from "../components/runtime/AttackFlow";
import { GovernanceOverview } from "../components/runtime/GovernanceOverview";
import { boardAtSeq } from "../runtime/event-reducer";
import { useSessionRuntime } from "../runtime/session-store";
import { MODE_PROFILES } from "../modes";

type Props = { taskId: string; mode: "runtime" | "replay"; onReplay: () => void };

const statusLabels: Record<string, string> = { created: "Created", running: "Running", paused: "Paused", blocked: "Blocked", completed: "Completed", failed: "Failed", cancelled: "Cancelled" };

export function SessionRuntimePage({ taskId, mode, onReplay }: Props) {
  const { snapshot, connection, error, refresh } = useSessionRuntime(taskId);
  const [busy, setBusy] = useState<"pause" | "resume" | "cancel" | "hint" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [hintOpen, setHintOpen] = useState(false);
  const [hint, setHint] = useState("");

  if (!snapshot) return <section className="runtime-workspace"><RuntimeLoading error={error} onRetry={() => void refresh()} /></section>;

  const replayBoard = mode === "replay" ? boardAtSeq(snapshot, snapshot.latest_seq) : { board: snapshot.board, available: true };
  const projected = { ...snapshot, board: replayBoard.board };
  const profile = MODE_PROFILES[snapshot.task.mode];
  const activeSolvers = snapshot.solvers.filter((solver) => ["starting", "running", "waiting"].includes(solver.status)).length;
  const provenFlags = snapshot.task.mode === "ctf" ? snapshot.flags.filter((flag) => Boolean(flag.evidence_artifact_id)) : [];

  const control = async (action: "pause" | "resume" | "cancel") => {
    setBusy(action); setNotice(null);
    try {
      await runtimeApi.control(taskId, action);
      setNotice(action === "pause" ? "Pause accepted. The current Agent turn will stop at its boundary." : `${action} accepted; waiting for the Session event.`);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Runtime control failed");
    } finally {
      setBusy(null); setConfirmCancel(false);
    }
  };

  const submitHint = async (event: FormEvent) => {
    event.preventDefault();
    if (!hint.trim()) return;
    setBusy("hint"); setNotice(null);
    try {
      await runtimeApi.hint(taskId, hint.trim());
      setHint(""); setHintOpen(false); setNotice("提示已提交，会加入 Solver Session 上下文。");
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Hint submission failed");
    } finally { setBusy(null); }
  };

  return <section className={`runtime-workspace breach-runtime-page ${mode === "replay" ? "is-replay" : ""}`}>
    <header className="runtime-command-header">
      <div className="runtime-identity">
        <div className="runtime-kicker"><span>{profile.label} runtime</span><i>/</i><code>{snapshot.task.id}</code></div>
        <div className="runtime-title-row"><h1>{snapshot.task.name}</h1><span className={`session-state ${snapshot.session.status}`} data-testid="session-status"><i />{mode === "replay" ? "Replay" : statusLabels[snapshot.session.status] || snapshot.session.status}</span></div>
        <button className="runtime-target" title={snapshot.task.target} onClick={() => void navigator.clipboard?.writeText(snapshot.task.target)}><span>{snapshot.task.target}</span><small>Copy</small></button>
      </div>
      <div className="runtime-command-side">
        <div className="runtime-facts">
          {snapshot.task.mode === "ctf" ? <span data-testid="challenge-status"><b>{snapshot.challenge.status}</b><small>Challenge</small></span> : <span><b>{profile.label}</b><small>Mode</small></span>}<span><b>{activeSolvers}</b><small>Solvers</small></span><span><b>{snapshot.session.turn_count}/{snapshot.session.max_turns}</b><small>Turns</small></span><span><b>{snapshot.artifacts.length}</b><small>Artifacts</small></span><span><b>{snapshot.latest_seq}</b><small>Events</small></span>
          <em className={`connection-state ${connection}`}><i />{connection}</em>
        </div>
        <div className="runtime-actions">
          <a href={runtimeApi.reportUrl(taskId)} target="_blank" rel="noreferrer">Report ↗</a>
          {mode === "runtime" ? <button onClick={onReplay}>Replay</button> : null}
          {mode === "runtime" ? <button onClick={() => setHintOpen(true)}>+ Hint</button> : null}
          {mode === "runtime" && snapshot.session.status === "running" ? <button disabled={busy !== null} onClick={() => void control("pause")}>Pause</button> : null}
          {mode === "runtime" && ["paused", "blocked"].includes(snapshot.session.status) ? <button disabled={busy !== null} onClick={() => void control("resume")}>Resume</button> : null}
          {mode === "runtime" && !["completed", "cancelled"].includes(snapshot.session.status) ? <button className="cancel-action" disabled={busy !== null} onClick={() => setConfirmCancel(true)}>取消</button> : null}
        </div>
      </div>
    </header>

    {provenFlags.length ? <div className="runtime-proof-banner" data-testid="flag-hero"><span>✓ Solver found a result</span>{provenFlags.map((flag) => <a key={flag.value} href={runtimeApi.artifactUrl(taskId, flag.evidence_artifact_id)} target="_blank" rel="noreferrer"><code>{flag.value}</code> · artifact ↗</a>)}</div> : null}
    {error ? <div className="runtime-toast error" role="alert">Live sync degraded: {error}<button onClick={() => void refresh()}>Retry</button></div> : null}
    {notice ? <div className="runtime-toast" role="status">{notice}<button aria-label="关闭通知" onClick={() => setNotice(null)}>×</button></div> : null}
    {mode === "replay" ? <div className="replay-readonly">回放模式：只读取已存 AgentEvent，不会发出控制、提示或目标请求。</div> : null}

    <GovernanceOverview snapshot={projected} />
    <AttackFlow snapshot={projected} mode={mode} />

    {hintOpen ? <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setHintOpen(false); }}>
      <form className="runtime-dialog hint-dialog" role="dialog" aria-modal="true" aria-labelledby="hint-title" onSubmit={submitHint}>
        <header><div><span className="eyebrow">Human guidance</span><h2 id="hint-title">Add context to Memory</h2></div><button type="button" aria-label="关闭提示" onClick={() => setHintOpen(false)}>×</button></header>
        <p>补充题面、已知路径或失败信息。提示会直接发送给持久 Solver Session。</p>
        <label>补充提示<textarea aria-label="补充提示" autoFocus maxLength={800} value={hint} onChange={(event) => setHint(event.target.value)} placeholder="What should the solvers know?" /><small>{hint.length}/800</small></label>
        <footer><button type="button" onClick={() => setHintOpen(false)}>Cancel</button><button className="primary" disabled={busy === "hint" || !hint.trim()}>{busy === "hint" ? "Submitting…" : "提交提示"}</button></footer>
      </form>
    </div> : null}

    {confirmCancel ? <div className="drawer-backdrop"><section className="runtime-dialog" role="dialog" aria-modal="true" aria-labelledby="cancel-title"><header><div><span className="eyebrow">Session control</span><h2 id="cancel-title">取消这个 Session？</h2></div><button aria-label="关闭取消确认" onClick={() => setConfirmCancel(false)}>×</button></header><p>当前 Solver 会话会停止，并记录最终状态事件。</p><footer><button onClick={() => setConfirmCancel(false)}>返回</button><button className="primary danger" disabled={busy === "cancel"} onClick={() => void control("cancel")}>{busy === "cancel" ? "正在请求…" : "确认取消"}</button></footer></section></div> : null}
  </section>;
}

export { redact } from "../components/runtime/RuntimePanels";
