import { useState } from "react";
import { runtimeApi } from "../api/runtime";
import { BoardPanel, ControlBar, EvidencePanel, HintComposer, ReplayControls, RuntimeHeader, RuntimeLoading, Timeline } from "../components/runtime/RuntimePanels";
import { ResizableWorkspace } from "../components/runtime/ResizableWorkspace";
import { boardAtSeq } from "../runtime/event-reducer";
import { useSessionRuntime } from "../runtime/session-store";

type Props = { taskId: string; mode: "runtime" | "replay"; onReplay: () => void };

export function SessionRuntimePage({ taskId, mode, onReplay }: Props) {
  const { snapshot, connection, error, refresh } = useSessionRuntime(taskId);
  const [selectedArtifact, setSelectedArtifact] = useState<string | null>(null); const [selectedEvent, setSelectedEvent] = useState<number | null>(null); const [busy, setBusy] = useState<"pause" | "resume" | "cancel" | "hint" | null>(null); const [notice, setNotice] = useState<string | null>(null); const [confirmCancel, setConfirmCancel] = useState(false);
  if (!snapshot) return <section className="runtime-workspace"><RuntimeLoading error={error} onRetry={() => void refresh()} /></section>;
  const replayCursor = mode === "replay" ? (selectedEvent ?? snapshot.latest_seq) : null;
  const replayBoard = mode === "replay" ? boardAtSeq(snapshot, replayCursor) : { board: snapshot.board, available: true };
  const control = async (action: "pause" | "resume" | "cancel") => { setBusy(action); setNotice(null); try { await runtimeApi.control(taskId, action); setNotice(action === "pause" ? "暂停请求已接受；当前 action 会在安全边界收口后暂停。" : `${action} 请求已接受，等待运行时事件确认。`); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "控制请求失败"); } finally { setBusy(null); setConfirmCancel(false); } };
  const addHint = async (content: string) => { setBusy("hint"); setNotice(null); try { await runtimeApi.hint(taskId, content); setNotice("提示已提交，等待策略记忆事件吸收。"); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "提示提交失败"); throw reason; } finally { setBusy(null); } };
  return <section className={`runtime-workspace ${mode === "replay" ? "is-replay" : ""}`}><RuntimeHeader snapshot={snapshot} connection={connection} replay={mode === "replay"} onReplay={onReplay} />{error ? <div className="inline-error" role="alert">实时状态同步异常：{error}<button className="text-button" onClick={() => void refresh()}>重试</button></div> : null}{notice ? <div className="runtime-notice" role="status">{notice}</div> : null}{mode === "runtime" ? <ControlBar status={snapshot.session.status} busy={busy} onPause={() => void control("pause")} onResume={() => void control("resume")} onCancel={() => setConfirmCancel(true)} /> : <><ReplayControls snapshot={snapshot} onSelect={setSelectedEvent} selected={selectedEvent} />{!replayBoard.available ? <div className="runtime-notice" role="status">该历史会话没有按 seq 保存策略板快照；为避免伪造历史状态，此位置不展示当前策略板。</div> : null}</>}<ResizableWorkspace board={<BoardPanel hypotheses={replayBoard.board.hypotheses} memory={replayBoard.board.memory} onArtifact={setSelectedArtifact} />} timeline={<Timeline events={snapshot.events} actions={snapshot.actions} selected={selectedEvent} onSelect={setSelectedEvent} />} evidence={<EvidencePanel taskId={taskId} artifacts={snapshot.artifacts} flags={snapshot.flags} findings={snapshot.findings} events={snapshot.events} selectedArtifact={selectedArtifact} onSelect={setSelectedArtifact} />} />{mode === "runtime" ? <HintComposer pending={busy === "hint"} onSubmit={addHint} /> : <div className="replay-banner">回放模式：只读取已存 AgentEvent，不会发出控制、提示或目标请求。</div>}{confirmCancel ? <div className="dialog-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="cancel-title"><h2 id="cancel-title">取消这个 Session？</h2><p>取消请求会交给 Runtime Manager；它会记录终止原因并发出最终事件。</p><div><button className="secondary-button" onClick={() => setConfirmCancel(false)}>返回</button><button className="danger-button" disabled={busy === "cancel"} onClick={() => void control("cancel")}>{busy === "cancel" ? "正在请求…" : "确认取消"}</button></div></section></div> : null}</section>;
}

export { redact } from "../components/runtime/RuntimePanels";
