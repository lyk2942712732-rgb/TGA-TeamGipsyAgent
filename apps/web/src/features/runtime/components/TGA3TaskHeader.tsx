import { ChevronRight, Download, RefreshCw, Square, Trash2 } from "lucide-react";
import { useState } from "react";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import type { TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import type { TGA3Connection } from "../use-tga3-runtime";
import { agentStateLabel, formatTime, stateLabel, stateTone } from "../tga3-view";
import { MODE_PROFILES } from "../../../modes";

export function TGA3TaskHeader({ snapshot, connection, busy, onRefresh, onStop, onDelete }: { snapshot: TGA3RuntimeSnapshot; connection: TGA3Connection; busy: boolean; onRefresh: () => void; onStop: () => void; onDelete: () => void }) {
  const { task, agents, blackboard } = snapshot;
  const [usageOpen, setUsageOpen] = useState(false);
  const reported = agents.filter(agent => agent.metadata?.token_usage?.reported);
  const totalTokens = reported.reduce((sum, agent) => sum + (agent.metadata?.token_usage?.total_tokens ?? 0), 0);
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
      {task.state === "scouting" && agents.find(agent => agent.role === "scout") ? <Fact label="Scout 阶段" value={agentStateLabel(agents.find(agent => agent.role === "scout")!)} /> : null}
      <div className="tga3-token-usage">
        <button type="button" className="tga3-task-fact tga3-token-toggle" aria-label="展开各智能体 Token 用量" aria-expanded={usageOpen} aria-controls="task-token-breakdown" onClick={() => setUsageOpen(value => !value)} title="累计输入与输出 Token，包含缓存输入；按 CLI 已上报用量统计">
          <span><small>累计 Token</small><b>{reported.length ? totalTokens.toLocaleString("en-US") : "未上报"}</b></span><ChevronRight size={15} />
        </button>
        {usageOpen ? <div id="task-token-breakdown" className="tga3-token-breakdown" role="group" aria-label="各智能体累计 Token">
          {agents.map(agent => <span className="tga3-task-fact" key={agent.agent_id} title={agent.metadata?.token_usage ? `输入 ${agent.metadata.token_usage.input_tokens.toLocaleString("en-US")} / 输出 ${agent.metadata.token_usage.output_tokens.toLocaleString("en-US")}` : "尚无原生用量记录"}><small>{agent.display_name}</small><b>{agent.metadata?.token_usage?.reported ? agent.metadata.token_usage.total_tokens.toLocaleString("en-US") : "未上报"}</b></span>)}
          {reported.length < agents.length ? <small className="tga3-token-note">总数仅含已上报用量</small> : null}
        </div> : null}
      </div>
    </div>
  </header>;
}

function Fact({ label, value }: { label: string; value: string }) { return <span className="tga3-task-fact"><small>{label}</small><b>{value}</b></span>; }
function connectionLabel(value: TGA3Connection) { return ({ loading: "连接中", live: "实时轮询", reconnecting: "重连中", offline: "已停止轮询" } as Record<TGA3Connection, string>)[value]; }
