import { ArrowRight, Bot, ChevronRight, CirclePlay, Database, FileText, Layers, Plus, Zap } from "lucide-react";
import type { AttentionItem } from "../api/tga3-attention";
import type { SystemHealth } from "../api/tga3-system";
import type { TGA3TaskListItem } from "../api/tga3-tasks";
import { EmptyState } from "../components/ui/EmptyState";
import { MODE_PROFILES } from "../modes";
import { statusLabel } from "../shared/status";

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
  const active = tasks.filter((task) => ["scouting", "starting", "running", "finalizing", "reporting"].includes(task.state));
  const completed = tasks.filter((task) => task.state === "completed");
  const failed = tasks.filter((task) => task.state === "failed");
  const primaryActive = active[0] ?? null;

  return (
    <div className="ref-page dashboard-ref ops-overview">
      <header className="ops-overview-head">
        <div>
          <h1>工作台</h1>
          <p className="ops-head-meta">
            <span>活动 {active.length}</span>
            <i>·</i>
            <span>待处理 {attention.length}</span>
            <i>·</i>
            <span>已完成 {completed.length}</span>
            {failed.length ? (
              <>
                <i>·</i>
                <span className="tone-bad">异常 {failed.length}</span>
              </>
            ) : null}
          </p>
        </div>
        <div className="button-row">
          <button type="button" className="ref-secondary-button" onClick={onTasks}>
            任务库 ({tasks.length})
          </button>
          <button type="button" className="ref-primary-button" onClick={onNew}>
            <Plus size={14} />
            创建任务
          </button>
        </div>
      </header>

      {/* Adaptive Hero Band: Live Operations if active, else Centered Quick Launch */}
      {primaryActive ? (
        <section className="ops-live-band" aria-label="活跃作战态势">
          <div className="ops-band-bar">
            <span className="ops-band-title">
              <i className="running-dot" aria-hidden="true" />
              LIVE OPERATIONS · 活跃作战 ({active.length})
            </span>
            <span className="ops-band-meta">最近更新 {relativeTime(primaryActive.updated_at)}</span>
          </div>
          <div className="ops-live-list">
            {active.slice(0, 4).map((task) => (
              <div key={task.id} className="ops-live-row">
                <button type="button" className="ops-live-main" onClick={() => onTask(task.id)}>
                  <span className="ops-live-name">
                    <CirclePlay size={15} className="ops-accent-icon" aria-hidden="true" />
                    <strong>{task.title}</strong>
                    <span className="ref-chip tone-info">{modeLabel(task.scene_id)}</span>
                    <span className="ref-chip tone-ok">{statusLabel(task.state)}</span>
                  </span>
                  <span className="ops-live-telemetry">
                    <span>黑板 #{task.blackboard_seq ?? 0}</span>
                    <span>事件 #{task.event_seq ?? 0}</span>
                    <span>{relativeTime(task.updated_at)}</span>
                  </span>
                </button>
                <button type="button" className="ref-primary-button" onClick={() => onTask(task.id)}>
                  进入作战室 <ArrowRight size={13} />
                </button>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <section className="ops-idle-launchpad" aria-label="极速发射台">
          <div className="ops-idle-copy">
            <span className="eyebrow">IDLE · READY FOR OPERATION</span>
            <h2>{tasks.length ? "当前无运行中任务，随时发起新作战" : "开始你的第一个 TGA3 作战任务"}</h2>
            <p>提供题目描述与附件，Scout 先行整理情报，四个异构 CLI Worker 基于共享黑板并行验证，Reporter 输出报告与经验卡。</p>
          </div>
          <div className="button-row">
            <button type="button" className="ref-primary-button" onClick={onNew}>
              <Zap size={14} />
              创建并启动任务
            </button>
            <button type="button" className="ref-secondary-button" onClick={onSystem}>
              系统诊断
            </button>
          </div>
        </section>
      )}

      {/* Hairline-Divided 2-Column Operational Panes */}
      <div className="ops-split-grid">
        <section className="ops-pane">
          <header className="ops-pane-head">
            <h2>需要处理 ({attention.length})</h2>
            <button type="button" className="ref-link-button" onClick={onApprovals}>
              全部 <ChevronRight size={13} />
            </button>
          </header>
          {attention.length ? (
            <ul className="attention-items">
              {attention.slice(0, 6).map((item) => (
                <li key={item.id}>
                  <span className={`severity-chip ${item.kind.includes("failed") ? "tone-high" : "tone-medium"}`}>
                    {item.kind === "agent_paused" ? "暂停" : "失败"}
                  </span>
                  <div className="attention-copy">
                    <strong>{item.title}</strong>
                    <small>
                      {item.task_title}
                      {item.agent_id ? ` · ${item.agent_id}` : ""}
                    </small>
                  </div>
                  <time>{relativeTime(item.created_at)}</time>
                  <button type="button" className="ref-secondary-button" onClick={() => onTask(item.task_id)}>
                    处理
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState label="当前无待处理中断" />
          )}
        </section>

        <section className="ops-pane">
          <header className="ops-pane-head">
            <h2>最近完成 ({completed.length})</h2>
            <button type="button" className="ref-link-button" onClick={onReports}>
              复盘库 <ChevronRight size={13} />
            </button>
          </header>
          {completed.length ? (
            <ul className="report-rows">
              {completed.slice(0, 6).map((task) => (
                <li key={task.id}>
                  <span className="report-icon">
                    <FileText size={14} />
                  </span>
                  <button type="button" className="report-main" onClick={() => onTask(task.id)}>
                    <strong>{task.title}</strong>
                  </button>
                  <span className="chip tone-neutral">{modeLabel(task.scene_id)}</span>
                  <time>{relativeTime(task.updated_at)}</time>
                  <em className="report-status">完成</em>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState label="暂无已完成任务" />
          )}
        </section>
      </div>

      <section className="ops-pane ops-health-strip">
        <header className="ops-pane-head">
          <h2>系统健康遥测</h2>
          <button type="button" className="ref-link-button" onClick={onSystem}>
            系统诊断 <ChevronRight size={13} />
          </button>
        </header>
        <ul className="system-rows">
          {(health?.components ?? []).map((item) => (
            <li key={item.id}>
              <span className="system-row-icon">
                {item.id === "models" ? <Layers size={14} /> : item.id === "runtime" ? <Bot size={14} /> : <Database size={14} />}
              </span>
              <strong>{item.label}</strong>
              <span className="system-row-status">
                <small className="system-row-note">{item.detail}</small>
                <em
                  className={`system-row-value tone-${
                    item.status === "unavailable" ? "bad" : item.status === "degraded" ? "warn" : "ok"
                  }`}
                >
                  {item.status === "unavailable" ? "异常" : item.status === "degraded" ? "降级" : "正常"}
                </em>
              </span>
            </li>
          ))}
        </ul>
      </section>
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
