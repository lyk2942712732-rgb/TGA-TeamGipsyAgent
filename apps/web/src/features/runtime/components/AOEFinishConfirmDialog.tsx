import { CheckCircle2, Radar, ShieldCheck, Square } from "lucide-react";
import { useState } from "react";
import type { AOEFinishConfirm, AOEScoutWatch } from "../../../runtime/tga3-runtime";

export function AOEFinishConfirmDialog({
  open,
  taskTitle,
  confirm,
  watch,
  busy,
  error,
  onContinueStandby,
  onFinish,
  onMinimize,
}: {
  open: boolean;
  taskTitle: string;
  confirm: AOEFinishConfirm;
  watch: AOEScoutWatch | null;
  busy: boolean;
  error: string | null;
  onContinueStandby: (autoStandbyFuture: boolean) => void;
  onFinish: (skipReports: boolean) => void;
  onMinimize: () => void;
}) {
  const [autoStandbyFuture, setAutoStandbyFuture] = useState(false);

  if (!open) return null;

  const total = confirm.wave_question_count ?? 0;
  const solved = confirm.solved_count ?? 0;
  const abandoned = confirm.abandoned_count ?? 0;
  const pendingReports = confirm.pending_reports ?? 0;
  const hasScript = Boolean(watch?.probe_script_present);
  const probeInterval = watch?.probe_interval_seconds ?? 45;

  return (
    <div className="ref-modal-backdrop" role="presentation" onClick={onMinimize}>
      <div
        className="ref-modal aoe-finish-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="aoe-finish-confirm-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="aoe-finish-header">
          <span className="aoe-finish-badge">
            <CheckCircle2 size={18} />
          </span>
          <div>
            <h3 id="aoe-finish-confirm-title">全部题目已收尾 — 是否继续任务？</h3>
            <p className="aoe-finish-subtitle">
              任务「{taskTitle}」当前批次题目已全部进入已解出或已放弃状态。
            </p>
          </div>
        </div>

        <div className="aoe-finish-stats">
          <div>
            <small>当前总题数</small>
            <strong>{total}</strong>
          </div>
          <div>
            <small>已解出</small>
            <strong className="is-solved">{solved}</strong>
          </div>
          <div>
            <small>已放弃</small>
            <strong className="is-abandoned">{abandoned}</strong>
          </div>
          <div>
            <small>后台报告中</small>
            <strong>{pendingReports}</strong>
          </div>
        </div>

        <div className={`aoe-finish-standby-note ${hasScript ? "is-sentinel" : "is-manual"}`}>
          {hasScript ? <ShieldCheck size={16} /> : <Radar size={16} />}
          <div>
            {hasScript ? (
              <>
                <strong>继续任务将保持默认零 Token 哨兵站岗</strong>
                <p>
                  不额外消耗 LLM Token，也不立即多跑一轮巡检；哨兵按每 {probeInterval} 秒零 Token
                  探测新题，发现放题瞬间自动唤醒 Scout 收题并调度 Worker 开干。
                </p>
              </>
            ) : (
              <>
                <strong>当前未检测到零 Token 探测脚本</strong>
                <p>
                  选择继续任务后，任务会保持存活待命（不会自动开启 LLM 定时巡检以免误耗 Token）；
                  你可以随时在顶部点击「唤醒 Scout 巡检」或按需开启定时巡检。
                </p>
              </>
            )}
          </div>
        </div>

        <label className="aoe-finish-checkbox">
          <input
            type="checkbox"
            checked={autoStandbyFuture}
            disabled={busy}
            onChange={(event) => setAutoStandbyFuture(event.target.checked)}
          />
          <span>后续波次全部完题后自动保持站岗，不再重复弹窗</span>
        </label>

        {error ? (
          <p className="aoe-finish-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="aoe-finish-actions">
          <button
            type="button"
            className="ref-primary-button"
            disabled={busy}
            onClick={() => onContinueStandby(autoStandbyFuture)}
          >
            <ShieldCheck size={15} />
            {busy ? "处理中…" : "继续任务，Scout 站岗候题"}
          </button>
          <button
            type="button"
            className="tga3-danger-button"
            disabled={busy}
            onClick={() => onFinish(pendingReports === 0)}
          >
            <Square size={14} />
            {pendingReports > 0 ? "等待报告完成并结束任务" : "立即结束任务"}
          </button>
        </div>

        <div className="aoe-finish-footer-links">
          <button type="button" className="text-button" disabled={busy} onClick={onMinimize}>
            先收起弹窗，查看题目与黑板
          </button>
          {pendingReports > 0 ? (
            <button
              type="button"
              className="text-button is-danger"
              disabled={busy}
              onClick={() => onFinish(true)}
            >
              跳过剩余报告，立即强制结束
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
