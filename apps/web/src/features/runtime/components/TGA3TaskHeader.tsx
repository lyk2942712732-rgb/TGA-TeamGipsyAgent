import {
  ArrowLeft,
  ChevronRight,
  Copy,
  Download,
  MoreHorizontal,
  PanelRight,
  RefreshCw,
  RotateCcw,
  Square,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import { MODE_PROFILES } from "../../../modes";
import type { TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { agentStateLabel, stateLabel, stateTone } from "../tga3-view";
import type { TGA3Connection } from "../use-tga3-runtime";

export function TGA3TaskHeader({
  snapshot,
  connection,
  busy,
  inspectorOpen,
  onBack,
  onToggleInspector,
  onRefresh,
  onStop,
  onRestart,
  onClone,
  onDelete,
}: {
  snapshot: TGA3RuntimeSnapshot;
  connection: TGA3Connection;
  busy: boolean;
  inspectorOpen?: boolean;
  onBack?: () => void;
  onToggleInspector?: () => void;
  onRefresh: () => void;
  onStop: () => void;
  onRestart?: () => void;
  onClone?: () => void;
  onDelete: () => void;
}) {
  const { task, agents, blackboard } = snapshot;
  const [usageOpen, setUsageOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onDocClick(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    }
    window.addEventListener("mousedown", onDocClick);
    return () => window.removeEventListener("mousedown", onDocClick);
  }, [menuOpen]);

  const reported = agents.filter((agent) => agent.metadata?.token_usage?.reported);
  const totalTokens = reported.reduce((sum, agent) => sum + (agent.metadata?.token_usage?.total_tokens ?? 0), 0);
  const active = agents.filter((agent) => ["starting", "running"].includes(agent.actual_state)).length;
  const findings = blackboard.filter((entry) => entry.kind === "finding").length;
  const intel = blackboard.filter((entry) => entry.kind === "intel").length;
  const finals = blackboard.filter((entry) => entry.kind === "final_candidate").length;
  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(task.state);
  const scoutAgent = task.state === "scouting" ? agents.find((agent) => agent.role === "scout") : null;
  const aoeSolved = snapshot.aoe ? snapshot.aoe.questions.filter((q) => q.status === "solved").length : 0;
  const aoeStandbyLabel =
    snapshot.aoe && !terminal && snapshot.aoe.collection_complete
      ? snapshot.aoe.scout_collecting
        ? "增量收集中"
        : snapshot.aoe.scout_watch?.state === "error_paused"
          ? "哨兵已熔断"
          : snapshot.aoe.scout_watch?.state === "probing"
            ? "哨兵探测中"
            : snapshot.aoe.scout_watch?.enabled
              ? snapshot.aoe.scout_watch.mode === "sentinel"
                ? "零 Token 哨兵"
                : "定时 LLM 巡检"
              : snapshot.aoe.scout_watch && !snapshot.aoe.scout_watch.probe_script_present
                ? "无脚本·手动"
                : "已暂停"
      : null;

  return (
    <header className="tga3-task-header cockpit-telemetry-bar">
      {onBack ? (
        <>
          <button type="button" className="text-button" onClick={onBack}>
            <ArrowLeft size={13} /> 任务库
          </button>
          <span className="tele-divider" />
        </>
      ) : null}

      <span className={`fleet-dot tone-${stateTone(task.state)}`} title={stateLabel(task.state)} />

      <div className="cockpit-tele-title">
        <h1>{task.title}</h1>
        <span className="ref-chip tone-accent">
          {MODE_PROFILES[task.scene_id as keyof typeof MODE_PROFILES]?.label ?? task.scene_id}
        </span>
      </div>

      <span className="tele-divider" />

      <div className="tga3-task-facts">
        {snapshot.aoe ? (
          <>
            <Fact label="解出" value={`${aoeSolved}/${snapshot.aoe.questions.length}`} />
            <Fact label="活动" value={`${active}/${agents.length}`} />
          </>
        ) : (
          <>
            <Fact label="Intel" value={String(intel)} />
            <Fact label="Finding" value={String(findings)} />
            <Fact label="候选" value={String(finals)} />
          </>
        )}
        <Fact label="同步" value={connectionLabel(connection)} />
        {scoutAgent ? <Fact label="Scout" value={agentStateLabel(scoutAgent)} /> : null}
        {aoeStandbyLabel ? <Fact label="站岗" value={aoeStandbyLabel} /> : null}

        <div className="tga3-token-usage">
          <button
            type="button"
            className="tga3-task-fact tga3-token-toggle"
            aria-label="展开各智能体 Token 用量"
            aria-expanded={usageOpen}
            aria-controls="task-token-breakdown"
            onClick={() => setUsageOpen((value) => !value)}
            title="累计输入与输出 Token，包含缓存输入；按 CLI 已上报用量统计"
          >
            <span>
              <small>Token </small>
              <b>{reported.length ? totalTokens.toLocaleString("en-US") : "未上报"}</b>
            </span>
            <ChevronRight size={12} />
          </button>
          {usageOpen ? (
            <div id="task-token-breakdown" className="tga3-token-breakdown" role="group" aria-label="各智能体累计 Token">
              {agents.map((agent) => (
                <span
                  className="tga3-task-fact"
                  key={agent.agent_id}
                  style={{ justifyContent: "space-between", width: "100%" }}
                  title={
                    agent.metadata?.token_usage
                      ? `输入 ${agent.metadata.token_usage.input_tokens.toLocaleString("en-US")} / 输出 ${agent.metadata.token_usage.output_tokens.toLocaleString("en-US")}`
                      : "尚无原生用量记录"
                  }
                >
                  <small>{agent.display_name}</small>
                  <b>
                    {agent.metadata?.token_usage?.reported
                      ? agent.metadata.token_usage.total_tokens.toLocaleString("en-US")
                      : "未上报"}
                  </b>
                </span>
              ))}
              {reported.length < agents.length ? <small className="subtle">总数仅含已上报用量</small> : null}
            </div>
          ) : null}
        </div>
      </div>

      <div className="tga3-task-actions">
        {onToggleInspector ? (
          <button
            type="button"
            className="ref-secondary-button"
            aria-expanded={inspectorOpen}
            onClick={onToggleInspector}
          >
            <PanelRight size={12} />
            {inspectorOpen ? "收起 Inspector" : "展开 Inspector"}
          </button>
        ) : null}

        {terminal && onRestart ? (
          <button type="button" className="ref-primary-button" disabled={busy} onClick={onRestart}>
            <RotateCcw size={12} />
            重新启动
          </button>
        ) : null}
        {terminal && onClone ? (
          <button type="button" className="ref-secondary-button" disabled={busy} onClick={onClone}>
            <Copy size={12} />
            基于此任务新建
          </button>
        ) : null}
        {!terminal ? (
          <button type="button" className="tga3-danger-button" disabled={busy} onClick={onStop}>
            <Square size={11} />
            停止任务
          </button>
        ) : null}

        <div className="tele-overflow-wrap" ref={menuRef}>
          <button
            type="button"
            className="ref-secondary-button"
            aria-label="更多任务操作"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
          >
            <MoreHorizontal size={14} />
          </button>
          {menuOpen ? (
            <div className="tele-overflow-menu" role="menu">
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  onRefresh();
                }}
              >
                <RefreshCw size={13} /> 立即刷新快照
              </button>
              {snapshot.aoe ? (
                <a href={`/tasks/${encodeURIComponent(task.id)}/runtime?tab=report`} onClick={() => setMenuOpen(false)}>
                  <Download size={13} /> 查看逐题报告
                </a>
              ) : task.state === "completed" ? (
                <a href={tga3RuntimeApi.reportUrl(task.id)} target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>
                  <Download size={13} /> 下载 Markdown 报告
                </a>
              ) : null}
              {!terminal && onClone ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setMenuOpen(false);
                    onClone();
                  }}
                >
                  <Copy size={13} /> 基于此任务新建
                </button>
              ) : null}
              <button
                type="button"
                className="is-danger"
                disabled={busy}
                onClick={() => {
                  setMenuOpen(false);
                  onDelete();
                }}
              >
                <Trash2 size={13} /> 删除任务
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <span className="tga3-task-fact">
      <small>{label}</small>
      <b>{value}</b>
    </span>
  );
}
function connectionLabel(value: TGA3Connection) {
  return ({ loading: "连接中", live: "实时", reconnecting: "重连中", offline: "离线" } as Record<TGA3Connection, string>)[value];
}
