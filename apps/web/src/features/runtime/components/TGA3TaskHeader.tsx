import { Download, RefreshCw, Square } from "lucide-react";
import { runtimeApi } from "../../../runtime/api-v2";
import type { TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import type { TGA3Connection } from "../use-tga3-runtime";
import { formatTime, stateLabel, stateTone } from "../tga3-view";

export function TGA3TaskHeader({ snapshot, connection, busy, onRefresh, onStop }: { snapshot: TGA3RuntimeSnapshot; connection: TGA3Connection; busy: boolean; onRefresh: () => void; onStop: () => void }) {
  const { task, agents, blackboard } = snapshot;
  const active = agents.filter((agent) => ["starting", "running"].includes(agent.actual_state)).length;
  const findings = blackboard.filter((entry) => entry.kind === "finding").length;
  const finals = blackboard.filter((entry) => entry.kind === "final_candidate").length;
  const questions = blackboard.filter((entry) => entry.kind === "qa").length;
  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(task.state);
  return <header className="tga3-task-header">
    <div className="tga3-task-title">
      <div><span>TGA3 / BLACKBOARD RUNTIME</span><h1>{task.title}</h1><p>场景：{task.scene_id} · 创建于 {formatTime(task.created_at)}</p></div>
      <div className="tga3-task-actions">
        <button type="button" className="ref-secondary-button" onClick={onRefresh}><RefreshCw size={15} />刷新</button>
        {task.state === "completed" ? <a className="ref-secondary-button" href={runtimeApi.reportUrl(task.id)} target="_blank" rel="noreferrer"><Download size={15} />下载报告</a> : null}
        {!terminal ? <button type="button" className="tga3-danger-button" disabled={busy} onClick={onStop}><Square size={14} />停止任务</button> : null}
      </div>
    </div>
    <div className="tga3-task-facts">
      <span className={`tga3-state-pill tone-${stateTone(task.state)}`}><i />{stateLabel(task.state)}</span>
      <Fact label="Agent" value={`${active} 活动 / ${agents.length} 总数`} /><Fact label="黑板" value={`${task.blackboard_seq} 条`} />
      <Fact label="Finding" value={`${findings}`} /><Fact label="Q&A" value={`${questions}`} /><Fact label="最终候选" value={`${finals}`} />
      <Fact label="同步" value={connectionLabel(connection)} /><Fact label="最后更新" value={formatTime(task.updated_at)} />
    </div>
  </header>;
}

function Fact({ label, value }: { label: string; value: string }) { return <span className="tga3-task-fact"><small>{label}</small><b>{value}</b></span>; }
function connectionLabel(value: TGA3Connection) { return ({ loading: "连接中", live: "实时轮询", reconnecting: "重连中", offline: "已停止轮询" } as Record<TGA3Connection, string>)[value]; }
