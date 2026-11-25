import { ArrowRight, ChevronRight, Plus } from "lucide-react";
import { useState } from "react";
import type { AttentionItem } from "../api/tga3-attention";
import type { SystemHealth } from "../api/tga3-system";
import type { TGA3TaskListItem } from "../api/tga3-tasks";
import { MODE_PROFILES } from "../modes";
import { statusLabel } from "../shared/status";

const QUICK_SCENES = ["WEB", "RE", "PWN", "MISC", "CRYPTO", "AI", "AOE"] as const;

export function DashboardPage({
  tasks,
  attention,
  health,
  onNew,
  onTask,
  onTasks,
  onApprovals,
  onSystem,
  onReports,
}: {
  tasks: TGA3TaskListItem[];
  attention: AttentionItem[];
  health?: SystemHealth;
  onNew: () => void;
  onTask: (taskId: string) => void;
  onTasks: () => void;
  onApprovals: () => void;
  onSystem: () => void;
  onReports: () => void;
}) {
  const [quickScene, setQuickScene] = useState<string>("WEB");
  const active = tasks.filter((task) => ["scouting", "starting", "running", "finalizing", "reporting"].includes(task.state));
  const completed = tasks.filter((task) => task.state === "completed");
  const failed = tasks.filter((task) => task.state === "failed");
  const primaryActive = active[0] ?? null;
  const recentTasks = tasks.filter((task) => task.id !== primaryActive?.id);

  return (
    <div className="ref-page dashboard-ref">
      <div className="page-frame">
        <div className="overview-scroll">
        <header className="overview-head">
          <div>
            <h1>工作台</h1>
            <div className="overview-head-sub">
              <span>LIVE {active.length}</span>
              <span className="subtle">·</span>
              <span>PENDING {attention.length}</span>
              <span className="subtle">·</span>
              <span>DONE {completed.length}</span>
              {failed.length ? (
                <>
                  <span className="subtle">·</span>
                  <span style={{ color: "var(--c-danger)" }}>ERR {failed.length}</span>
                </>
              ) : null}
            </div>
          </div>
          <div className="button-row">
            <button type="button" className="ref-secondary-button" onClick={onTasks}>
              全部任务 ({tasks.length})
            </button>
            <button type="button" className="ref-primary-button" onClick={onNew}>
              <Plus size={14} /> 新建任务
            </button>
          </div>
        </header>

        {/* Adaptive Hero: Live Operations Band vs Centered Quick Launchpad */}
        {primaryActive ? (
          <section className="live-ops-band" aria-label="活跃作战态势">
            <div className="proto-section-bar">
              <span className="proto-section-title">
                <i className="running-dot" aria-hidden="true" /> LIVE OPERATIONS · 活跃作战中 ({active.length})
              </span>
              <span className="mono muted" style={{ fontSize: "11px" }}>
                TASK #{primaryActive.id.slice(0, 8)} · 更新于 {relativeTime(primaryActive.updated_at)}
              </span>
            </div>

            <div className="live-ops-hero">
              <div className="live-ops-cell">
                <div className="live-task-title">
                  <button
                    type="button"
                    className="text-button"
                    style={{ padding: 0, fontSize: "15px", fontWeight: 600, color: "var(--c-text)" }}
                    onClick={() => onTask(primaryActive.id)}
                  >
                    {primaryActive.title}
                  </button>
                  <span className="ref-chip tone-accent">{modeLabel(primaryActive.scene_id)}</span>
                  <span className="ref-chip tone-ok">{statusLabel(primaryActive.state)}</span>
                </div>
                <div className="proto-progress" style={{ maxWidth: 420 }}>
                  <div className="proto-progress-fill is-ok" style={{ width: "58%" }} />
                </div>
                <div className="live-metrics-row">
                  <div className="kv-mini">
                    <span>ACTIVE</span>
                    <strong>{active.length}</strong>
                  </div>
                  <div className="kv-mini">
                    <span>BLACKBOARD</span>
                    <strong>#{primaryActive.blackboard_seq ?? 0}</strong>
                  </div>
                  <div className="kv-mini">
                    <span>EVENTS</span>
                    <strong>#{primaryActive.event_seq ?? 0}</strong>
                  </div>
                  <div className="kv-mini">
                    <span>COMPLETED</span>
                    <strong>{completed.length}</strong>
                  </div>
                </div>
              </div>

              <div className="live-ops-cell">
                <div className="caps muted mono" style={{ marginBottom: 8 }}>
                  {active.length > 1 ? `并行活跃任务 (${active.length})` : "当前作战通道概览"}
                </div>
                <div className="worker-mini-grid">
                  {active.length > 1 ? (
                    active.slice(0, 4).map((t) => (
                      <button key={t.id} type="button" className="worker-mini-item" onClick={() => onTask(t.id)}>
                        <span className="truncate">{t.title}</span>
                        <span className="mono subtle">{statusLabel(t.state)}</span>
                      </button>
                    ))
                  ) : (
                    <>
                      <div className="worker-mini-item">
                        <span className="mono">场景模式</span>
                        <span className="mono muted">{modeLabel(primaryActive.scene_id)}</span>
                      </div>
                      <div className="worker-mini-item">
                        <span className="mono">实时状态</span>
                        <span className="mono" style={{ color: "var(--c-success)" }}>
                          {statusLabel(primaryActive.state)}
                        </span>
                      </div>
                      <div className="worker-mini-item">
                        <span className="mono">共享黑板</span>
                        <span className="mono muted">#{primaryActive.blackboard_seq ?? 0}</span>
                      </div>
                      <div className="worker-mini-item">
                        <span className="mono">待处理中断</span>
                        <span
                          className="mono"
                          style={{ color: attention.length ? "var(--c-warning)" : "var(--c-text-muted)" }}
                        >
                          {attention.length} 条
                        </span>
                      </div>
                    </>
                  )}
                </div>
              </div>

              <div className="live-ops-cell">
                <button type="button" className="ref-primary-button" onClick={() => onTask(primaryActive.id)}>
                  进入 100vh 作战室 <ArrowRight size={13} />
                </button>
                <button type="button" className="ref-secondary-button" onClick={onNew}>
                  + 发起新任务
                </button>
              </div>
            </div>
          </section>
        ) : (
          <section className="idle-launchpad-wrap" aria-label="极速发射台">
            <div className="idle-launchpad">
              <div className="idle-lp-head">
                <span className="proto-section-title">QUICK LAUNCH · 极速发起任务</span>
                <span className="mono muted" style={{ fontSize: "11px" }}>
                  当前无运行中任务 · 选择场景即可进入两层发射台
                </span>
              </div>
              <div className="idle-lp-body">
                <div className="scene-seg-bar">
                  {QUICK_SCENES.map((code) => (
                    <button
                      key={code}
                      type="button"
                      className={`scene-seg-btn${quickScene === code ? " selected" : ""}`}
                      style={{ height: 30 }}
                      onClick={() => setQuickScene(code)}
                    >
                      <span className="scene-code">{code}</span>
                    </button>
                  ))}
                </div>
                <input
                  readOnly
                  onClick={onNew}
                  placeholder="点击输入任务标题、粘贴题面描述或拖放附件压缩包…"
                  style={{ cursor: "pointer" }}
                />
              </div>
              <div className="idle-lp-foot">
                <span className="mono muted">Scout → 4 Workers (共享黑板) → Reporter 报告与经验卡</span>
                <div className="button-row">
                  <button type="button" className="ref-secondary-button" onClick={onSystem}>
                    系统诊断
                  </button>
                  <button type="button" className="ref-primary-button" onClick={onNew}>
                    创建并启动任务 →
                  </button>
                </div>
              </div>
            </div>
          </section>
        )}

        {/* Bottom Half: 2-Column Hairline Panes */}
        <div className="overview-grid-2">
          <section className="pane-box">
            <div className="proto-section-bar">
              <span className="proto-section-title">RECENT OPERATIONS · 近期任务 ({tasks.length})</span>
              <button type="button" className="text-button" onClick={onTasks}>
                全部任务 <ChevronRight size={13} />
              </button>
            </div>
            {recentTasks.length ? (
              recentTasks.slice(0, 8).map((task) => {
                const dotClass =
                  task.state === "completed"
                    ? "tone-ok"
                    : task.state === "failed"
                      ? "tone-err"
                      : ["running", "scouting", "starting"].includes(task.state)
                        ? "tone-accent"
                        : "tone-muted";
                return (
                  <button
                    key={task.id}
                    type="button"
                    className="dense-row"
                    style={{ gridTemplateColumns: "14px minmax(0,1fr) 74px 90px 76px" }}
                    onClick={() => onTask(task.id)}
                  >
                    <i className={`sb-dot ${dotClass}`} />
                    <span className="truncate">
                      <strong>{task.title}</strong> <span className="mono subtle">#{task.id.slice(0, 8)}</span>
                    </span>
                    <span className="ref-chip">{modeLabel(task.scene_id)}</span>
                    <span className="mono muted">{statusLabel(task.state)}</span>
                    <span className="mono subtle" style={{ textAlign: "right" }}>
                      {relativeTime(task.updated_at)}
                    </span>
                  </button>
                );
              })
            ) : (
              <div className="tga3-empty">
                <p>暂无任务记录，点击上方按钮开始你的第一个任务。</p>
              </div>
            )}
          </section>

          <section className="pane-box">
            <div className="proto-section-bar">
              <span className="proto-section-title">ATTENTION & TELEMETRY · 中断与健康</span>
              <div className="button-row">
                <button type="button" className="text-button" onClick={onApprovals}>
                  中断 ({attention.length})
                </button>
                <button type="button" className="text-button" onClick={onReports}>
                  复盘库 <ChevronRight size={13} />
                </button>
              </div>
            </div>

            {attention.length ? (
              attention.slice(0, 4).map((item) => (
                <div
                  key={item.id}
                  className="dense-row"
                  style={{ gridTemplateColumns: "52px minmax(0,1fr) auto" }}
                >
                  <span className={`ref-chip ${item.kind.includes("failed") ? "tone-danger" : "tone-warn"}`}>
                    {item.kind === "agent_paused" ? "暂停" : "失败"}
                  </span>
                  <div className="truncate">
                    <strong>{item.title}</strong>
                    <div className="mono subtle truncate" style={{ fontSize: "11px" }}>
                      {item.task_title}
                      {item.agent_id ? ` · ${item.agent_id}` : ""}
                    </div>
                  </div>
                  <button type="button" className="ref-secondary-button" onClick={() => onTask(item.task_id)}>
                    处理
                  </button>
                </div>
              ))
            ) : (
              <div className="dense-row" style={{ gridTemplateColumns: "1fr" }}>
                <span className="muted">当前无待处理中断 · 所有舰队节点平稳</span>
              </div>
            )}

            {(health?.components ?? []).map((item) => (
              <div key={item.id} className="dense-row" style={{ gridTemplateColumns: "14px minmax(0,1fr) auto" }}>
                <i
                  className={`status-dot tone-${
                    item.status === "unavailable" ? "err" : item.status === "degraded" ? "warn" : "ok"
                  }`}
                />
                <span className="truncate">
                  <strong>{item.label}</strong>{" "}
                  <small className="mono subtle">{item.detail}</small>
                </span>
                <span className="mono muted" style={{ fontSize: "11px" }}>
                  {item.latencyMs !== null ? `${item.latencyMs}ms` : item.status === "unavailable" ? "异常" : "正常"}
                </span>
              </div>
            ))}
          </section>
        </div>
        </div>
      </div>
    </div>
  );
}

function modeLabel(mode: string) {
  return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode;
}
function relativeTime(value: string) {
  const minutes = Math.floor((Date.now() - new Date(value).getTime()) / 60000);
  if (!Number.isFinite(minutes) || minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  return new Date(value).toLocaleDateString("zh-CN");
}
