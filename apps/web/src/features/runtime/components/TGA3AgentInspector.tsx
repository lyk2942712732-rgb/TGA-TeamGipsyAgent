import { Bot, Box, CirclePause, CirclePlay, Paperclip, Send, Settings2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { deleteStagedInput, fetchAgentModelOptions, stageInput, type ProviderProtocol, type StagedAsset } from "../../../api/tasks";
import type { TaskMode } from "../../../modes";
import { runtimeApi } from "../../../runtime/api-v2";
import type { TGA3Agent, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { formatTime, latestPendingQuestion, protocolLabel, roleLabel, sdkLabel, stateLabel, stateTone } from "../tga3-view";
import { DialogueCard } from "./TGA3Workspace";

type InspectorTab = "overview" | "conversation" | "activity" | "control";
const TABS: Array<[InspectorTab, string]> = [["overview", "概览"], ["conversation", "对话"], ["activity", "活动"], ["control", "模型与控制"]];

export function TGA3AgentInspector({ snapshot, agent, openConversationNonce = 0, onChanged }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent | null; openConversationNonce?: number; onChanged: () => void }) {
  const [tab, setTab] = useState<InspectorTab>("overview");
  useEffect(() => { if (openConversationNonce) setTab("conversation"); }, [openConversationNonce]);
  if (!agent) return <aside className="tga3-agent-inspector"><div className="tga3-empty"><Bot /><h3>未选择 Agent</h3><p>从左侧选择一个 Agent 查看状态和对话。</p></div></aside>;
  return <aside className="tga3-agent-inspector">
    <header><div><span>AGENT INSPECTOR</span><h2>{agent.display_name}</h2><p>{agent.agent_id} · {roleLabel(agent)}</p></div><span className={`tga3-state-pill tone-${stateTone(agent.actual_state)}`}><i />{stateLabel(agent.actual_state)}</span></header>
    <div className="tga3-inspector-tabs" role="tablist">{TABS.map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>)}</div>
    <div className="tga3-inspector-body">
      {tab === "overview" ? <Overview agent={agent} /> : null}
      {tab === "conversation" ? <Conversation snapshot={snapshot} agent={agent} onChanged={onChanged} /> : null}
      {tab === "activity" ? <Activity snapshot={snapshot} agent={agent} /> : null}
      {tab === "control" ? <Controls snapshot={snapshot} agent={agent} onChanged={onChanged} /> : null}
    </div>
  </aside>;
}

function Overview({ agent }: { agent: TGA3Agent }) {
  const tools = agentTools(agent);
  return <div className="tga3-agent-overview">
    <Info title="运行身份"><Row label="角色" value={roleLabel(agent)} /><Row label="运行位置" value={agent.runtime_location === "container" ? "隔离容器" : "主服务"} /><Row label="SDK" value={sdkLabel(agent.sdk)} /><Row label="期望状态" value={stateLabel(agent.desired_state)} /><Row label="实际状态" value={stateLabel(agent.actual_state)} /></Info>
    <Info title="模型"><Row label="供应商" value={agent.provider_id} /><Row label="模型" value={agent.model_id} /><Row label="协议" value={protocolLabel(agent.protocol)} /></Info>
    {agent.runtime_location === "container" ? <Info title="容器会话"><Row label="Container" value={agent.container_id || "尚未分配"} /><Row label="Session" value={agent.session_id || "尚未建立"} /></Info> : null}
    <Info title="可用工具"><div className="tga3-tool-tags">{tools.map((tool) => <span key={tool}>{tool}</span>)}</div></Info>
    {agent.last_error ? <div className="tga3-agent-failure" role="alert"><strong>最近错误</strong><p>{agent.last_error}</p><small>{formatTime(agent.updated_at)}</small></div> : null}
  </div>;
}

function Conversation({ snapshot, agent, onChanged }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent; onChanged: () => void }) {
  const [text, setText] = useState(""); const [assets, setAssets] = useState<StagedAsset[]>([]); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const question = latestPendingQuestion(snapshot.dialogue, snapshot.task.state);
  const isQuestionChannel = Boolean(question && agent.role === "supervisor");
  const messages = useMemo(() => snapshot.dialogue.filter((message) => message.channel_agent_id === agent.agent_id || message.actor.agent_id === agent.agent_id || message.payload.agent_id === agent.agent_id), [snapshot.dialogue, agent.agent_id]);
  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(snapshot.task.state);
  async function addFiles(files: File[]) { setBusy(true); setNotice(null); try { const uploaded = await Promise.all(files.map((file) => stageInput(file))); setAssets((current) => [...current, ...uploaded]); } catch (reason) { setNotice(errorText(reason)); } finally { setBusy(false); } }
  async function removeAsset(asset: StagedAsset) { setAssets((current) => current.filter((item) => item.id !== asset.id)); await deleteStagedInput(asset.id).catch(() => undefined); }
  async function send() {
    if ((!text.trim() && !assets.length) || busy || terminal) return;
    setBusy(true); setNotice(null);
    try {
      if (isQuestionChannel && question) await runtimeApi.answerQuestion(snapshot.task.id, String(question.payload.question_id), text.trim(), assets);
      else await runtimeApi.solverMessage(snapshot.task.id, agent.agent_id, text.trim(), assets);
      setText(""); setAssets([]); setNotice(isQuestionChannel ? "回答已写入共享黑板，任务将继续运行。" : "提示已写入共享黑板并发送到该 Agent 通道。"); onChanged();
    } catch (reason) { setNotice(errorText(reason)); } finally { setBusy(false); }
  }
  return <div className="tga3-conversation">
    {isQuestionChannel && question ? <div className="tga3-question-banner"><strong>Supervisor 正在等待你的回答</strong><p>{question.text}</p><small>提问来源：{String(question.payload.origin_agent_id ?? "supervisor")}</small></div> : null}
    <div className="tga3-conversation-thread">{messages.length ? messages.map((message) => <DialogueCard key={message.id} message={message} compact />) : <p className="tga3-muted">该 Agent 暂无持久化对话。</p>}</div>
    <div className="tga3-composer">
      {assets.length ? <div className="tga3-composer-assets">{assets.map((asset) => <span key={asset.id}>{asset.originalName}<button aria-label={`移除 ${asset.originalName}`} onClick={() => void removeAsset(asset)}><X size={11} /></button></span>)}</div> : null}
      <textarea value={text} disabled={terminal} placeholder={isQuestionChannel ? "回答 Supervisor 的问题…" : `给 ${agent.display_name} 添加提示…`} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} />
      <footer><input ref={fileRef} hidden multiple type="file" accept="image/*,audio/*,video/*,.pdf,.txt,.md,.json,.csv" onChange={(event) => { void addFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} /><button type="button" disabled={busy || terminal} onClick={() => fileRef.current?.click()}><Paperclip size={14} />附件</button><span>支持图片、音视频和文档</span><button className="tga3-send" type="button" disabled={busy || terminal || (!text.trim() && !assets.length)} onClick={() => void send()}><Send size={14} /></button></footer>
    </div>{notice ? <p className="tga3-notice" role="status">{notice}</p> : null}
  </div>;
}

function Activity({ snapshot, agent }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent }) {
  const dialogue = snapshot.dialogue.filter((item) => item.channel_agent_id === agent.agent_id || item.actor.agent_id === agent.agent_id || item.payload.agent_id === agent.agent_id);
  const board = snapshot.blackboard.filter((item) => item.actor.agent_id === agent.agent_id || (Array.isArray(item.body.addressed_to) && item.body.addressed_to.includes(agent.agent_id)));
  return <div className="tga3-activity"><section><h3>对话事件 <small>{dialogue.length}</small></h3>{dialogue.length ? [...dialogue].reverse().map((message) => <DialogueCard key={message.id} message={message} compact />) : <p className="tga3-muted">暂无事件</p>}</section><section><h3>黑板写入 <small>{board.length}</small></h3>{board.length ? [...board].reverse().map((entry) => <article key={entry.id}><b>#{entry.seq} · {entry.kind}</b><p>{entry.topic}</p><small>{formatTime(entry.created_at)}</small></article>) : <p className="tga3-muted">暂无写入</p>}</section></div>;
}

function Controls({ snapshot, agent, onChanged }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent; onChanged: () => void }) {
  const [models, setModels] = useState<Array<{ provider_id: string; provider_name: string; model_id: string; model_name: string; protocol: ProviderProtocol }>>([]);
  const [selected, setSelected] = useState(`${agent.provider_id}::${agent.model_id}::${agent.protocol}`); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { let active = true; void fetchAgentModelOptions(snapshot.task.scene_id as TaskMode).then((value) => { if (!active) return; setModels(value.models.filter((model) => model.ready && (agent.sdk === "claude_agent" ? model.protocol === "anthropic" : model.protocol !== "anthropic"))); }).catch((reason) => active && setNotice(errorText(reason))); return () => { active = false; }; }, [snapshot.task.scene_id, agent.sdk]);
  useEffect(() => setSelected(`${agent.provider_id}::${agent.model_id}::${agent.protocol}`), [agent.provider_id, agent.model_id, agent.protocol]);
  async function change(value: string) { setSelected(value); const [provider, model, protocol] = value.split("::"); if (!provider || !model || !protocol) return; setBusy(true); setNotice(null); try { await runtimeApi.solverModel(snapshot.task.id, agent.agent_id, provider, model, protocol as ProviderProtocol); setNotice("模型已切换，将用于该 Agent 的后续周期。"); onChanged(); } catch (reason) { setNotice(errorText(reason)); } finally { setBusy(false); } }
  async function control(action: "pause" | "resume") { setBusy(true); setNotice(null); try { await runtimeApi.solverControl(snapshot.task.id, agent.agent_id, action); setNotice(action === "pause" ? "暂停请求已提交，将在下一个周期检查点生效。" : "Agent 已恢复运行。"); onChanged(); } catch (reason) { setNotice(errorText(reason)); } finally { setBusy(false); } }
  const worker = agent.role === "worker" && agent.runtime_location === "container"; const paused = ["paused", "pause_requested"].includes(agent.actual_state);
  return <div className="tga3-controls"><section><header><Settings2 size={17} /><h3>后续模型</h3></header><label>供应商 / 模型 / 协议<select value={selected} disabled={busy || !models.length} onChange={(event) => void change(event.target.value)}>{!models.length ? <option value={selected}>没有可用的兼容模型</option> : null}{models.map((model) => <option key={`${model.provider_id}::${model.model_id}::${model.protocol}`} value={`${model.provider_id}::${model.model_id}::${model.protocol}`}>{model.provider_name} / {model.model_name} / {protocolLabel(model.protocol)}</option>)}</select></label></section>
    <section><header><Box size={17} /><h3>执行控制</h3></header>{worker ? <button type="button" disabled={busy} onClick={() => void control(paused ? "resume" : "pause")}>{paused ? <CirclePlay size={16} /> : <CirclePause size={16} />}{paused ? "恢复 Worker" : "暂停 Worker"}</button> : <p className="tga3-muted">该 Agent 运行在主服务，由任务生命周期统一管理，不提供独立暂停。</p>}</section>{notice ? <p className="tga3-notice" role="status">{notice}</p> : null}
  </div>;
}

function Info({ title, children }: { title: string; children: ReactNode }) { return <section className="tga3-info"><h3>{title}</h3><dl>{children}</dl></section>; }
function Row({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
function agentTools(agent: TGA3Agent) { if (agent.role === "supervisor") return ["共享黑板", "Skills", "Q&A 转递"]; if (agent.role === "reporter") return ["共享黑板只读", "Skills", "Markdown Writeup"]; return ["共享黑板", "Skills", "Shell", "文件系统", "网络工具"]; }
function errorText(reason: unknown) { return reason instanceof Error ? reason.message : "操作失败"; }
