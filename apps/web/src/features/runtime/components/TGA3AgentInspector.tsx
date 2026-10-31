import { Bot, Box, CirclePause, CirclePlay, Settings2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import { fetchAgentModelOptions } from "../../../api/tga3-tasks";
import type { ProviderProtocol } from "../../../api/tga3-config";
import type { TaskMode } from "../../../modes";
import type { TGA3Agent, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { BOARD_KIND_LABELS, bodyText, formatTime, protocolLabel, roleLabel, sdkLabel, stateLabel, stateTone } from "../tga3-view";
import { EventCard } from "./TGA3Workspace";

type InspectorTab = "overview" | "activity" | "control";
const TABS: Array<[InspectorTab, string]> = [["overview", "概览"], ["activity", "活动"], ["control", "模型与控制"]];

export function TGA3AgentInspector({ snapshot, agent, onChanged }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent | null; onChanged: () => void }) {
  const [tab, setTab] = useState<InspectorTab>("overview");
  if (!agent) return <aside className="tga3-agent-inspector"><div className="tga3-empty"><Bot /><h3>未选择 Agent</h3><p>从上方运行图选择一个 Agent 查看状态和活动。</p></div></aside>;
  return <aside className="tga3-agent-inspector">
    <header><div><h2>{agent.display_name}</h2><p>{agent.agent_id} · {roleLabel(agent)}</p></div><span className={`tga3-state-pill tone-${stateTone(agent.actual_state)}`}><i />{stateLabel(agent.actual_state)}</span></header>
    <div className="tga3-inspector-tabs" role="tablist">{TABS.map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>)}</div>
    <div className="tga3-inspector-body">
      {tab === "overview" ? <Overview agent={agent} /> : null}
      {tab === "activity" ? <Activity snapshot={snapshot} agent={agent} /> : null}
      {tab === "control" ? <Controls snapshot={snapshot} agent={agent} onChanged={onChanged} /> : null}
    </div>
  </aside>;
}

function Overview({ agent }: { agent: TGA3Agent }) {
  const tools = agentTools(agent);
  return <div className="tga3-agent-overview">
    <Info title="运行身份"><Row label="角色" value={roleLabel(agent)} /><Row label="运行位置" value={agent.runtime_location === "container" ? "隔离容器" : "主服务"} /><Row label="执行后端" value={sdkLabel(agent.sdk)} /><Row label="期望状态" value={stateLabel(agent.desired_state)} /><Row label="实际状态" value={stateLabel(agent.actual_state)} /></Info>
    <Info title="模型"><Row label="供应商" value={agent.provider_id} /><Row label="模型" value={agent.model_id} /><Row label="协议" value={protocolLabel(agent.protocol)} /></Info>
    {agent.role === "worker" && agent.metadata?.blackboard_cursor !== undefined ? <Info title="黑板同步"><Row label="已确认读取" value={`#${agent.metadata.blackboard_cursor}`} /><Row label="最近读取" value={agent.metadata.blackboard_read_at ? formatTime(new Date(agent.metadata.blackboard_read_at * 1000).toISOString()) : "尚未读取"} /></Info> : null}
    {agent.runtime_location === "container" ? <Info title="容器会话"><Row label="Container" value={agent.container_id || "尚未分配"} /><Row label="Session" value={agent.session_id || "尚未建立"} /></Info> : null}
    <Info title="可用工具"><div className="tga3-tool-tags">{tools.map((tool) => <span key={tool}>{tool}</span>)}</div></Info>
    {agent.actual_state === "failed" && agent.last_error ? <div className="tga3-agent-failure" role="alert"><strong>最近错误</strong><p>{agent.last_error}</p><small>{formatTime(agent.updated_at)}</small></div> : null}
  </div>;
}

function Activity({ snapshot, agent }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent }) {
  const events = snapshot.events.filter((item) => item.channel_agent_id === agent.agent_id || item.actor.agent_id === agent.agent_id || item.payload.agent_id === agent.agent_id);
  const board = snapshot.blackboard.filter((item) => item.actor.agent_id === agent.agent_id || (Array.isArray(item.body.addressed_to) && item.body.addressed_to.includes(agent.agent_id)));
  return <div className="tga3-activity"><section><h3>执行事件 <small>{events.length}</small></h3>{events.length ? [...events].reverse().map((message) => <EventCard key={message.id} message={message} compact />) : <p className="tga3-muted">暂无事件</p>}</section><section><h3>黑板写入 <small>{board.length}</small></h3>{board.length ? [...board].reverse().map((entry) => <article key={entry.id}><b>#{entry.seq} · {BOARD_KIND_LABELS[entry.kind]}</b><p>{bodyText(entry)}</p><small>{formatTime(entry.created_at)}</small></article>) : <p className="tga3-muted">暂无写入</p>}</section></div>;
}

function Controls({ snapshot, agent, onChanged }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent; onChanged: () => void }) {
  const [models, setModels] = useState<Array<{ provider_id: string; provider_name: string; model_id: string; model_name: string; protocol: ProviderProtocol }>>([]);
  const [selected, setSelected] = useState(`${agent.provider_id}::${agent.model_id}::${agent.protocol}`); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { let active = true; void fetchAgentModelOptions(snapshot.task.scene_id as TaskMode).then((value) => { if (!active) return; setModels(value.models.filter((model) => model.ready && (agent.sdk === "claude_cli" ? model.protocol === "anthropic" : agent.sdk === "codex_cli" ? model.protocol === "openai_responses" : model.protocol !== "anthropic"))); }).catch((reason) => active && setNotice(errorText(reason))); return () => { active = false; }; }, [snapshot.task.scene_id, agent.sdk]);
  useEffect(() => setSelected(`${agent.provider_id}::${agent.model_id}::${agent.protocol}`), [agent.provider_id, agent.model_id, agent.protocol]);
  async function change(value: string) { setSelected(value); const [provider, model, protocol] = value.split("::"); if (!provider || !model || !protocol) return; setBusy(true); setNotice(null); try { await tga3RuntimeApi.setWorkerModel(snapshot.task.id, agent.agent_id, provider, model, protocol as ProviderProtocol); setNotice("模型已切换，将用于该 Agent 的后续周期。"); onChanged(); } catch (reason) { setNotice(errorText(reason)); } finally { setBusy(false); } }
  async function control(action: "pause" | "resume") { setBusy(true); setNotice(null); try { await tga3RuntimeApi.setWorkerState(snapshot.task.id, agent.agent_id, action); setNotice(action === "pause" ? "暂停请求已提交，将在下一个周期检查点生效。" : "Agent 已恢复运行。"); onChanged(); } catch (reason) { setNotice(errorText(reason)); } finally { setBusy(false); } }
  const worker = agent.role === "worker" && agent.runtime_location === "container"; const paused = ["paused", "pause_requested"].includes(agent.actual_state);
  return <div className="tga3-controls"><section><header><Settings2 size={17} /><h3>后续模型</h3></header><label>供应商 / 已启用模型<select value={models.some((model) => `${model.provider_id}::${model.model_id}::${model.protocol}` === selected) ? selected : ""} disabled={busy || !worker || !models.length} onChange={(event) => void change(event.target.value)}><option value="" disabled>{models.length ? "请选择已启用模型" : "没有可用的兼容模型"}</option>{models.map((model) => <option key={`${model.provider_id}::${model.model_id}::${model.protocol}`} value={`${model.provider_id}::${model.model_id}::${model.protocol}`}>{model.provider_name} / {model.model_name}</option>)}</select></label></section>
    <section><header><Box size={17} /><h3>执行控制</h3></header>{worker ? <button type="button" disabled={busy} onClick={() => void control(paused ? "resume" : "pause")}>{paused ? <CirclePlay size={16} /> : <CirclePause size={16} />}{paused ? "恢复 Worker" : "暂停 Worker"}</button> : <p className="tga3-muted">该角色由任务生命周期启动一次性容器，不提供独立暂停或运行中切换模型。</p>}</section>{notice ? <p className="tga3-notice" role="status">{notice}</p> : null}
  </div>;
}

function Info({ title, children }: { title: string; children: ReactNode }) { return <section className="tga3-info"><h3>{title}</h3><dl>{children}</dl></section>; }
function Row({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
function agentTools(agent: TGA3Agent) { if (agent.role === "scout") return ["Intel 收集", "Kali 侦察工具", "Shell", "文件系统", "Skills"]; if (agent.role === "reporter") return ["共享黑板只读", "Skills", "Markdown Writeup"]; return ["共享黑板", "Skills", "Shell", "文件系统", "网络工具"]; }
function errorText(reason: unknown) { return reason instanceof Error ? reason.message : "操作失败"; }
