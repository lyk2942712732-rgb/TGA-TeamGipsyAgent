import { AlertTriangle, Pause, Play, Radar, RefreshCw, ShieldCheck, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import type { AOEOverview, AOEScoutWatch } from "../../../runtime/tga3-runtime";

const LLM_INTERVAL_OPTIONS = [
  { value: "", label: "定时 LLM 巡检：关闭（手动）" },
  { value: "180", label: "每 3 分钟 LLM 巡检" },
  { value: "300", label: "每 5 分钟 LLM 巡检" },
  { value: "600", label: "每 10 分钟 LLM 巡检" },
  { value: "900", label: "每 15 分钟 LLM 巡检" },
];

function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}:${String(rem).padStart(2, "0")}`;
}

export function AOEScoutStandbyBar({
  taskId,
  taskState,
  overview,
  onChanged,
}: {
  taskId: string;
  taskState: string;
  overview: AOEOverview;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(taskState);
  if (terminal) return null;

  const watch: AOEScoutWatch | null = overview.scout_watch ?? null;
  const collectionComplete = Boolean(overview.collection_complete);
  const scoutCollecting = Boolean(overview.scout_collecting || !collectionComplete);
  const canStopInitial = !collectionComplete && overview.questions.length > 0;

  async function runAction(action: () => Promise<void>, successMessage: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await action();
      setFeedback(successMessage);
      onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  const countdownSeconds =
    watch?.enabled && typeof watch.next_check_at === "number" ? Math.max(0, watch.next_check_at - now) : null;

  let tone: "active" | "sentinel" | "warn" | "idle" = "idle";
  let headline = "Scout 待命";
  if (scoutCollecting) {
    tone = "active";
    headline = collectionComplete
      ? `Scout 正在增量收集（第 ${watch?.scan_round ?? 2} 轮）`
      : "Scout 正在首轮收集题目";
  } else if (watch?.state === "probing") {
    tone = "sentinel";
    headline = "零 Token 哨兵正在探测新题…";
  } else if (watch?.state === "error_paused") {
    tone = "warn";
    headline = `零 Token 哨兵已熔断暂停（连续失败 ${watch.consecutive_failures} 次）`;
  } else if (watch?.enabled && watch.mode === "sentinel") {
    tone = "sentinel";
    headline = `零 Token 哨兵站岗中${countdownSeconds !== null ? ` · ${formatCountdown(countdownSeconds)} 后探测` : ""}`;
  } else if (watch?.enabled && watch.mode === "llm_poll") {
    tone = "active";
    headline = `定时 LLM 巡检中${countdownSeconds !== null ? ` · ${formatCountdown(countdownSeconds)} 后唤醒` : ""}`;
  } else if (watch && !watch.probe_script_present) {
    tone = "idle";
    headline = "Scout 手动待命（未检测到零 Token 探测脚本）";
  } else {
    tone = "idle";
    headline = "Scout 自动站岗已暂停（手动模式）";
  }

  const llmSelectValue = watch?.mode === "llm_poll" && watch.enabled && watch.llm_interval_seconds
    ? String(watch.llm_interval_seconds)
    : "";

  return (
    <section className={`aoe-standby-bar tone-${tone}`} aria-label="Scout 站岗与增量收题控制">
      <div className="aoe-standby-main">
        <div className="aoe-standby-status">
          <span className="aoe-standby-icon" aria-hidden="true">
            {tone === "warn" ? (
              <AlertTriangle size={16} />
            ) : tone === "sentinel" ? (
              <ShieldCheck size={16} />
            ) : (
              <Radar size={16} />
            )}
          </span>
          <div className="aoe-standby-copy">
            <div className="aoe-standby-title-row">
              <strong>{headline}</strong>
              {watch?.mode === "sentinel" && watch.enabled ? (
                <span className="aoe-standby-chip is-zero-token">
                  0 Token · 已探测 {watch.sentinel_checks_total} 次
                </span>
              ) : null}
              {overview.phase === "standby" ? (
                <span className="aoe-standby-chip is-standby">全题已收尾 · 候题中</span>
              ) : null}
            </div>
            <small>
              {error
                ? error
                : feedback
                  ? feedback
                  : watch?.last_result
                    ? watch.last_result
                    : collectionComplete
                      ? "随时可手动唤醒 Scout 增量搜集新题，不影响正在解题的 Worker。"
                      : "首轮收集完成后，若有平台探测脚本将自动切入零 Token 哨兵站岗。"}
            </small>
          </div>
        </div>

        <div className="aoe-standby-actions">
          {!collectionComplete ? (
            canStopInitial ? (
              <button
                type="button"
                className="ref-secondary-button"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await tga3RuntimeApi.stopScout(taskId);
                    },
                    "已提前结束首轮收集，Worker 将继续解已登记题目。",
                  )
                }
              >
                <Square size={13} />
                结束首轮收集
              </button>
            ) : null
          ) : scoutCollecting ? (
            <button
              type="button"
              className="tga3-danger-button"
              disabled={busy}
              onClick={() =>
                void runAction(
                  async () => {
                    await tga3RuntimeApi.stopScout(taskId);
                  },
                  "已停止本轮 Scout 增量收集。",
                )
              }
            >
              <Square size={13} />
              停止 Scout
            </button>
          ) : (
            <>
              <button
                type="button"
                className="ref-primary-button"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await tga3RuntimeApi.wakeScout(taskId);
                    },
                    "已唤醒 Scout 开始新一轮增量收题。",
                  )
                }
              >
                <RefreshCw size={14} />
                唤醒 Scout 巡检
              </button>

              {watch?.enabled ? (
                <button
                  type="button"
                  className="ref-secondary-button"
                  disabled={busy}
                  onClick={() =>
                    void runAction(
                      async () => {
                        await tga3RuntimeApi.stopScout(taskId);
                      },
                      "已暂停自动站岗，切换为手动模式。",
                    )
                  }
                >
                  <Pause size={14} />
                  停止站岗
                </button>
              ) : watch?.probe_script_present ? (
                <button
                  type="button"
                  className="ref-secondary-button"
                  disabled={busy}
                  onClick={() =>
                    void runAction(
                      async () => {
                        await tga3RuntimeApi.configureScoutWatch(taskId, "enable_sentinel");
                      },
                      "已开启零 Token 哨兵站岗。",
                    )
                  }
                >
                  <Play size={14} />
                  开启零 Token 哨兵
                </button>
              ) : null}

              <select
                aria-label="Scout 定时 LLM 巡检间隔"
                className="aoe-standby-select"
                disabled={busy}
                value={llmSelectValue}
                onChange={(event) => {
                  const val = event.target.value;
                  if (!val) {
                    void runAction(
                      async () => {
                        await tga3RuntimeApi.configureScoutWatch(
                          taskId,
                          watch?.probe_script_present ? "enable_sentinel" : "disable",
                        );
                      },
                      watch?.probe_script_present
                        ? "已切回零 Token 哨兵站岗。"
                        : "已关闭定时 LLM 巡检。",
                    );
                  } else {
                    const sec = Number(val);
                    void runAction(
                      async () => {
                        await tga3RuntimeApi.configureScoutWatch(taskId, "enable_llm_poll", sec);
                      },
                      `已开启每 ${Math.round(sec / 60)} 分钟定时 LLM 巡检。`,
                    );
                  }
                }}
              >
                {LLM_INTERVAL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
