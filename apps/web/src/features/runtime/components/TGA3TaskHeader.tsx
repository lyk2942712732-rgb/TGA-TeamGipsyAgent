import { Download, RefreshCw, Square, Trash2 } from "lucide-react";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import type { TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import type { TGA3Connection } from "../use-tga3-runtime";
import { formatTime, stateLabel, stateTone } from "../tga3-view";
import { MODE_PROFILES } from "../../../modes";

export function TGA3TaskHeader({ snapshot, connection, busy, onRefresh, onStop, onDelete }: { snapshot: TGA3RuntimeSnapshot; connection: TGA3Connection; busy: boolean; onRefresh: () => void; onStop: () => void; onDelete: () => void }) {
  const { task, agents, blackboard } = snapshot;
  const active = agents.filter((agent) => ["starting", "running"].includes(agent.actual_state)).length;
  const findings = blackboard.filter((entry) => entry.kind === "finding").length;
  const intel = blackboard.filter((entry) => entry.kind === "intel").length;
  const finals = blackboard.filter((entry) => entry.kind === "final_candidate").length;
  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(task.state);
  return <header className="tga3-task-header">
    <div className="tga3-task-title">
      <div><h1>{task.title}</h1><p>{MODE_PROFILES[task.scene_id as keyof typeof MODE_PROFILES]?.label ?? task.scene_id} · 创建于 {formatTime(task.created_at)}</p></div>
      <div className="tga3-task-actions">
        <button type="button" className="ref-secondary-button" onClick={onRefresh}><RefreshCw size={15} />刷新</button>
        {task.state === "completed" ? <a className="ref-secondary-button" href={tga3RuntimeApi.reportUrl(task.id)} target="_blank" rel="noreferrer"><Download size={15} />下载报告</a> : null}
        {!terminal ? <button type="button" className="tga3-danger-button" disabled={busy} onClick={onStop}><Square size={14} />停止任务</button> : null}
        <button type="button" className="ref-secondary-button tga3-delete-task-button" disabled={busy} onClick={onDelete}><Trash2 size={14} />删除任务</button>
      </div>
    </div>
    <div className="tga3-task-facts">
      <span className={`tga3-state-pill tone-${stateTone(task.state)}`}><i />{stateLabel(task.state)}</span>
      <Fact label="Agent" value={`${active} 活动 / ${agents.length} 总数`} />
      <Fact label="Intel" value={`${intel}`} /><Fact label="Finding" value={`${findings}`} /><Fact label="最终候选" value={`${finals}`} />
      <Fact label="同步" value={connectionLabel(connection)} />
    </div>
  </header>;
}

function Fact({ label, value }: { label: string; value: string }) { return <span className="tga3-task-fact"><small>{label}</small><b>{value}</b></span>; }
function connectionLabel(value: TGA3Connection) { return ({ loading: "连接中", live: "实时轮询", reconnecting: "重连中", offline: "已停止轮询" } as Record<TGA3Connection, string>)[value]; }
