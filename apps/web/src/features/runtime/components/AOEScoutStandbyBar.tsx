import { AlertTriangle, Pause, Play, Radar, RefreshCw, ShieldCheck, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import type { AOEOverview, AOEScoutWatch } from "../../../runtime/tga3-runtime";

const LLM_FALLBACK_OPTIONS = [
  { value: "", label: "定时 LLM 巡检兜底：关闭" },
  { value: "180", label: "兜底：每 3 分钟 LLM 巡检（耗 Token）" },
  { value: "300", label: "兜底：每 5 分钟 LLM 巡检（耗 Token）" },
  { value: "600", label: "兜底：每 10 分钟 LLM 巡检（耗 Token）" },
  { value: "900", label: "兜底：每 15 分钟 LLM 巡检（耗 Token）" },
];

export type ResolvedScoutStandby = {
  status: "collecting" | "watching" | "stopped";
  method: "sentinel" | "llm_poll" | "unavailable";
  unavailableReason: "no_script" | "probe_failed" | null;
  probing: boolean;
  tone: "active" | "sentinel" | "warn" | "idle";
  shortLabel: string;
  watch: AOEScoutWatch | null;
};

/** Single source of truth mapping AOEOverview to the 3-state Scout lifecycle + 2 watch methods. */
export function resolveScoutStandby(overview: AOEOverview): ResolvedScoutStandby {
  const watch = overview.scout_watch ?? null;
  const collectionComplete = Boolean(overview.collection_complete);
  const collecting = Boolean(overview.scout_collecting || !collectionComplete || watch?.status === "collecting");
  if (collecting) {
    return {
      status: "collecting",
      method: watch?.watch_method ?? (watch?.probe_script_present ? "sentinel" : "unavailable"),
      unavailableReason: null,
      probing: false,
      tone: "active",
      shortLabel: collectionComplete ? "增量收集中" : "首轮收集中",
      watch,
    };
  }

  let status: "watching" | "stopped";
  let method: "sentinel" | "llm_poll" | "unavailable";
  let unavailableReason: "no_script" | "probe_failed" | null = watch?.unavailable_reason ?? null;
  const probing = Boolean(watch?.probing || watch?.state === "probing");

  if (watch?.status === "stopped") {
    status = "stopped";
    method = watch.watch_method ?? (watch.probe_script_present ? "sentinel" : "unavailable");
  } else if (watch?.status === "watching") {
    status = "watching";
    method = watch.watch_method ?? (watch.probe_script_present ? "sentinel" : "unavailable");
  } else if (watch?.state === "error_paused") {
    status = "watching";
    method = "unavailable";
    unavailableReason = "probe_failed";
  } else if (watch?.enabled) {
    status = "watching";
    method = watch.mode === "llm_poll" ? "llm_poll" : "sentinel";
    unavailableReason = null;
  } else if (watch && !watch.probe_script_present) {
    status = "watching";
    method = "unavailable";
    unavailableReason = "no_script";
  } else {
    status = "stopped";
    method = watch?.probe_script_present ? "sentinel" : "unavailable";
  }

  if (status === "stopped") {
    return { status, method, unavailableReason, probing: false, tone: "idle", shortLabel: "已停止", watch };
  }
  if (method === "sentinel") {
    return {
      status,
      method,
      unavailableReason: null,
      probing,
      tone: "sentinel",
      shortLabel: probing ? "哨兵探测中" : "零 Token 哨兵",
      watch,
    };
  }
  if (method === "llm_poll") {
    return {
      status,
      method,
      unavailableReason: null,
      probing: false,
      tone: "active",
      shortLabel: "定时巡检兜底",
      watch,
    };
  }
  if (unavailableReason === "probe_failed") {
    return {
      status,
      method,
      unavailableReason,
      probing: false,
      tone: "warn",
      shortLabel: "哨兵已熔断",
      watch,
    };
  }
  return {
    status,
    method,
    unavailableReason: "no_script",
    probing: false,
    tone: "warn",
    shortLabel: "无脚本·需选兜底",
    watch,
  };
}

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

  const resolved = resolveScoutStandby(overview);
  const { status, method, unavailableReason, probing, tone, watch } = resolved;
  const collectionComplete = Boolean(overview.collection_complete);
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
    status === "watching" &&
    (method === "sentinel" || method === "llm_poll") &&
    typeof watch?.next_check_at === "number"
      ? Math.max(0, watch.next_check_at - now)
      : null;

  let headline = "Scout 已停止";
  if (status === "collecting") {
    headline = collectionComplete
      ? `Scout 正在收集题目（第 ${watch?.scan_round ?? 2} 轮）`
      : "Scout 正在首轮收集题目";
  } else if (status === "watching") {
    if (method === "sentinel") {
      headline = probing
        ? "站岗中 · 零 Token 脚本哨兵探测中…"
        : `站岗中 · 零 Token 脚本哨兵${countdownSeconds !== null ? ` · ${formatCountdown(countdownSeconds)} 后探测` : ""}`;
    } else if (method === "llm_poll") {
      headline = `站岗中 · 定时 LLM 巡检兜底${countdownSeconds !== null ? ` · ${formatCountdown(countdownSeconds)} 后唤醒` : ""}`;
    } else if (unavailableReason === "probe_failed") {
      headline = `站岗中 · 脚本哨兵连续失败 ${watch?.consecutive_failures ?? 3} 次已熔断`;
    } else {
      headline = "站岗中 · 未检测到探测脚本（请选择兜底方式）";
    }
  }

  const llmSelectValue =
    status === "watching" && method === "llm_poll" && watch?.llm_interval_seconds
      ? String(watch.llm_interval_seconds)
      : "";
  // Periodic LLM polling is strictly a fallback when the probe script is unavailable (or already active as fallback).
  const showLlmFallbackSelect =
    status === "watching" && (method === "unavailable" || method === "llm_poll");

  const defaultHint =
    status === "collecting"
      ? collectionComplete
        ? "增量收集完成后将自动回到站岗态，不影响正在解题的 Worker。"
        : "首轮收集完成后，有探测脚本将默认进入零 Token 脚本哨兵站岗。"
      : status === "stopped"
        ? "已人工停止收集与站岗；需要继续收题或候题时，请点击唤醒收集或恢复站岗。"
        : method === "sentinel"
          ? "零 Token 脚本哨兵默认运行中，发现新题将自动唤醒 Scout 收集。"
          : method === "llm_poll"
            ? "当前由定时 LLM 巡检兜底；若后续收集补齐了探测脚本，将自动升回零 Token 脚本哨兵。"
            : unavailableReason === "probe_failed"
              ? "脚本哨兵已熔断（绝不自动耗 Token）：可重试脚本、唤醒 Scout 修复脚本，或手动开启定时巡检兜底。"
              : "未生成零 Token 探测脚本：可唤醒 Scout 收集/补脚本，或手动开启定时 LLM 巡检兜底。";

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
              {status === "watching" && method === "sentinel" ? (
                <span className="aoe-standby-chip is-zero-token">
                  0 Token · 已探测 {watch?.sentinel_checks_total ?? 0} 次
                </span>
              ) : null}
              {overview.phase === "standby" ? (
                <span className="aoe-standby-chip is-standby">全题已收尾 · 候题中</span>
              ) : null}
            </div>
            <small>{error ?? feedback ?? watch?.last_result ?? defaultHint}</small>
          </div>
        </div>

        <div className="aoe-standby-actions">
          {status === "collecting" ? (
            !collectionComplete ? (
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
                      "已停止首轮收集，Worker 将继续解已登记题目。",
                    )
                  }
                >
                  <Square size={13} />
                  停止收集
                </button>
              ) : null
            ) : (
              <button
                type="button"
                className="tga3-danger-button"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await tga3RuntimeApi.stopScout(taskId);
                    },
                    "已停止本轮 Scout 收集。",
                  )
                }
              >
                <Square size={13} />
                停止收集
              </button>
            )
          ) : status === "stopped" ? (
            <>
              <button
                type="button"
                className="ref-primary-button"
                disabled={busy}
                title="立即运行一轮 Scout 收集（消耗 Token）"
                onClick={() =>
                  void runAction(
                    async () => {
                      await tga3RuntimeApi.wakeScout(taskId);
                    },
                    "已唤醒 Scout 开始增量收集。",
                  )
                }
              >
                <RefreshCw size={14} />
                唤醒收集
              </button>
              <button
                type="button"
                className="ref-secondary-button"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await tga3RuntimeApi.configureScoutWatch(taskId, "resume_watch");
                    },
                    watch?.probe_script_present
                      ? "已恢复站岗（零 Token 脚本哨兵）。"
                      : "已恢复站岗态（当前无探测脚本，可选择兜底方式）。",
                  )
                }
              >
                <Play size={14} />
                恢复站岗
              </button>
            </>
          ) : (
            <>
              {method === "unavailable" && unavailableReason === "probe_failed" ? (
                <button
                  type="button"
                  className="ref-primary-button"
                  disabled={busy}
                  onClick={() =>
                    void runAction(
                      async () => {
                        await tga3RuntimeApi.configureScoutWatch(taskId, "enable_sentinel");
                      },
                      "已重新拉起零 Token 脚本哨兵。",
                    )
                  }
                >
                  <Play size={14} />
                  重试脚本哨兵
                </button>
              ) : null}

              <button
                type="button"
                className={
                  method === "unavailable" && unavailableReason === "no_script"
                    ? "ref-primary-button"
                    : "ref-secondary-button"
                }
                disabled={busy}
                title="立即唤醒 Scout 执行一轮收集或修复探测脚本（消耗 Token）"
                onClick={() =>
                  void runAction(
                    async () => {
                      await tga3RuntimeApi.wakeScout(taskId);
                    },
                    "已唤醒 Scout 开始增量收集。",
                  )
                }
              >
                <RefreshCw size={14} />
                {method === "unavailable" && unavailableReason === "probe_failed"
                  ? "唤醒 Scout 修复脚本"
                  : method === "unavailable"
                    ? "唤醒 Scout 收集/补脚本"
                    : "唤醒收集"}
              </button>

              {showLlmFallbackSelect ? (
                <select
                  aria-label="Scout 定时 LLM 巡检兜底间隔"
                  className="aoe-standby-select"
                  disabled={busy}
                  value={llmSelectValue}
                  onChange={(event) => {
                    const val = event.target.value;
                    if (!val) {
                      void runAction(
                        async () => {
                          await tga3RuntimeApi.configureScoutWatch(taskId, "disable_llm_poll");
                        },
                        "已关闭定时 LLM 巡检兜底。",
                      );
                    } else {
                      const sec = Number(val);
                      void runAction(
                        async () => {
                          await tga3RuntimeApi.configureScoutWatch(taskId, "enable_llm_poll", sec);
                        },
                        `已开启定时 LLM 巡检兜底（每 ${Math.round(sec / 60)} 分钟 · 耗 Token）。`,
                      );
                    }
                  }}
                >
                  {LLM_FALLBACK_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              ) : null}

              <button
                type="button"
                className="ref-secondary-button"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await tga3RuntimeApi.stopScout(taskId);
                    },
                    "已停止 Scout 站岗，进入静止态。",
                  )
                }
              >
                <Pause size={14} />
                停止站岗
              </button>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
