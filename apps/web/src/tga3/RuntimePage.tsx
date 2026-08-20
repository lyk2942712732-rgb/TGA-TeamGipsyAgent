import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot, Boxes, CheckCircle2, CirclePause, CirclePlay, Download, FileUp, Flag,
  Lightbulb, LoaderCircle, MessageSquareText, RefreshCw, Send, ShieldCheck,
  Square, UserRound,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { tga3Api } from "./api";
import { useTaskRuntime } from "./hooks";
import type { AgentRun, BlackboardEntry, DialogueMessage, ModelCatalog, SkillInfo } from "./types";
import { formatTime, shortId, StateBadge, stateLabel } from "./App";

type InspectorTab = "overview" | "dialogue" | "blackboard" | "skills";

export function RuntimePage() {
  const { taskId = "" } = useParams();
  const runtime = useTaskRuntime(taskId);
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState("worker-openai");
  const [tab, setTab] = useState<InspectorTab>("dialogue");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const models = useQuery({ queryKey: ["tga3", "models"], queryFn: tga3Api.models });
  const scenes = useQuery({ queryKey: ["tga3", "scenes"], queryFn: tga3Api.scenes });
  const skills = useQuery({ queryKey: ["tga3", "skills"], queryFn: tga3Api.skills });

  if (runtime.task.error) return <div className="error-box">{runtime.task.error.message}</div>;
  if (runtime.task.isLoading || !runtime.task.data) return <div className="runtime-loading"><LoaderCircle className="spin" />正在连接任务控制面</div>;
  const detail = runtime.task.data;
  const task = detail.task;
  const sceneName = scenes.data?.find((scene) => scene.id === task.scene_id)?.name ?? task.scene_id;
  const selected = detail.agents.find((agent) => agent.agent_id === selectedId) ?? detail.agents[0];
  if (!selected) return <div className="error-box">后端尚未创建任何 Agent 运行记录。</div>;

  const mutate = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setNotice("");
    try {
      await action();
      setNotice(success);
      await runtime.refresh();
      await client.invalidateQueries({ queryKey: ["tga3", "tasks"] });
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  return <section className="runtime-page">
    <header className="runtime-header">
      <div className="runtime-title"><Link to="/tasks">任务</Link><span>/</span><div><h1>{task.title}</h1><small>{sceneName} · {task.id}</small></div></div>
      <div className="runtime-metrics">
        <span><small>黑板</small><b>#{task.blackboard_seq}</b></span>
        <span><small>对话</small><b>#{task.dialogue_seq}</b></span>
        <span><small>连接</small><b className={`stream-${runtime.streamState}`}>{runtime.streamState === "online" ? "SSE 在线" : runtime.streamState === "connecting" ? "连接中" : "轮询补偿"}</b></span>
        <StateBadge state={task.state} />
        {task.state === "completed" ? <a className="outline-button" href={tga3Api.writeupDownloadUrl(task.id)}><Download size={15} />下载 Writeup</a> : null}
        {!['completed', 'failed', 'cancelled'].includes(task.state) ? <button className="danger-button" disabled={busy} onClick={() => void mutate(() => tga3Api.stopTask(task.id), "任务已停止")}><Square size={14} />停止任务</button> : null}
      </div>
    </header>
    {notice ? <div className="runtime-notice" role="status">{notice}<button onClick={() => setNotice("")}>×</button></div> : null}
    <div className="runtime-grid">
      <aside className="agent-rail" aria-label="Agent 列表">
        <header><div><Bot size={18} /><b>Agents</b></div><small>{detail.agents.filter((agent) => agent.role === "worker").length} Worker 并行</small></header>
        <div className="agent-tree">{detail.agents.map((agent, index) => <button className={selected.agent_id === agent.agent_id ? "active" : ""} key={agent.agent_id} onClick={() => { setSelectedId(agent.agent_id); setTab("dialogue"); }}>
          <span className={`agent-avatar avatar-${index}`}>{avatar(agent)}</span>
          <span><b>{agent.display_name}</b><small>{agent.role} · {agent.runtime_location}</small><em className={`agent-state state-${agent.actual_state}`}><i />{stateLabel(agent.actual_state)}</em></span>
        </button>)}</div>
        <footer><ShieldCheck size={15} /><span>状态来自后端 agent_runs</span></footer>
      </aside>

      <main className="blackboard-workspace">
        <header><div><ShieldCheck size={19} /><span><b>共享黑板</b><small>只展示经过契约校验的共享知识</small></span></div><button className="icon-button" aria-label="刷新" onClick={() => void runtime.refresh()}><RefreshCw size={16} /></button></header>
        <Blackboard entries={runtime.blackboard.data?.entries ?? []} loading={runtime.blackboard.isLoading} />
      </main>

      <aside className="agent-inspector">
        <header className="inspector-head">
          <div><span className="agent-avatar avatar-selected">{avatar(selected)}</span><span><b>{selected.display_name}</b><small>{selected.role}</small></span></div>
          <StateBadge state={selected.actual_state} />
        </header>
        <nav className="inspector-tabs">
          {([['overview', '概览'], ['dialogue', '对话'], ['blackboard', '黑板'], ['skills', 'Skills']] as const).map(([id, label]) => <button className={tab === id ? "active" : ""} key={id} onClick={() => setTab(id)}>{label}</button>)}
        </nav>
        <div className="inspector-body">
          {tab === "overview" ? <Overview taskId={task.id} agent={selected} models={models.data} busy={busy} mutate={mutate} /> : null}
          {tab === "dialogue" ? <Dialogue taskId={task.id} agent={selected} messages={runtime.messages} pendingQuestion={runtime.pendingQuestion} busy={busy} mutate={mutate} /> : null}
          {tab === "blackboard" ? <AgentBlackboard entries={runtime.blackboard.data?.entries ?? []} /> : null}
          {tab === "skills" ? <AgentSkills skills={skills.data ?? []} /> : null}
        </div>
      </aside>
    </div>
  </section>;
}

function Blackboard({ entries, loading }: { entries: BlackboardEntry[]; loading: boolean }) {
  const visible = [...entries].reverse();
  if (loading) return <div className="loading-box"><LoaderCircle className="spin" />正在同步黑板</div>;
  if (!visible.length) return <div className="empty-box"><ShieldCheck /><b>黑板还是空的</b><p>场景提示、用户描述、已验证 Finding、Supervisor 建议和 Q&A 会出现在这里。</p></div>;
  return <div className="blackboard-feed">{visible.map((entry) => <article className={`board-entry board-${entry.kind}`} key={entry.id}>
    <header><span>{entryIcon(entry.kind)}<b>{kindLabel(entry.kind)}</b></span><small>#{entry.seq} · {entry.actor.display_name} · {formatTime(entry.created_at)}</small></header>
    <EntryBody entry={entry} />
  </article>)}</div>;
}

function EntryBody({ entry }: { entry: BlackboardEntry }) {
  const body = entry.body;
  if (entry.kind === "qa") return <div className="qa-body"><p><b>Q</b>{text(body.question)}</p><p><b>A</b>{text(body.answer)}</p><small>问题来源：{text(body.origin_agent_id)}</small></div>;
  const primary = body.text ?? body.advice ?? body.claim ?? body.conclusion ?? body.name;
  const secondary = body.detail ?? body.rationale ?? body.description;
  return <div className="entry-copy"><p>{text(primary)}</p>{secondary ? <small>{text(secondary)}</small> : null}{entry.kind === "final_candidate" ? <strong><Flag size={14} />最终候选</strong> : null}</div>;
}

function Overview({ taskId, agent, models, busy, mutate }: {
  taskId: string; agent: AgentRun; models?: ModelCatalog; busy: boolean;
  mutate: (action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const allowed = models?.providers.filter((provider) => agent.sdk === "claude_agent" ? provider.protocol === "anthropic" : provider.protocol !== "anthropic") ?? [];
  const current = `${agent.provider_id}::${agent.model_id}`;
  return <div className="overview-stack">
    <Info title="SDK Runtime" value={agent.sdk} />
    <Info title="执行位置" value={agent.runtime_location === "container" ? (agent.container_id ? `容器 ${shortId(agent.container_id)}` : "容器尚未连接") : "Ubuntu 主服务"} />
    <Info title="Session" value={agent.session_id ? shortId(agent.session_id) : agent.runtime_location === "host" ? "由主服务管理" : "尚未建立"} />
    <Info title="当前模型" value={`${agent.provider_id}/${agent.model_id}`} />
    {agent.last_error ? <div className="agent-error"><b>最近错误</b><p>{agent.last_error}</p></div> : null}
    {agent.role === "worker" ? <label className="control-field">切换模型<select value={current} disabled={busy} onChange={(event) => {
      const [providerId, modelId] = event.target.value.split("::");
      if (providerId && modelId) void mutate(() => tga3Api.setAgentModel(taskId, agent.agent_id, providerId, modelId), `${agent.display_name} 模型已切换`);
    }}>{allowed.flatMap((provider) => provider.models.map((model) => <option key={`${provider.id}::${model.id}`} value={`${provider.id}::${model.id}`}>{provider.name} / {model.name}</option>))}</select></label> : null}
    {agent.role === "worker" ? agent.actual_state === "paused" ? <button className="primary-button full-button" disabled={busy} onClick={() => void mutate(() => tga3Api.resumeAgent(taskId, agent.agent_id), `${agent.display_name} 已恢复`)}><CirclePlay size={16} />恢复 Worker</button>
      : <button className="outline-button full-button" disabled={busy || !["running", "starting"].includes(agent.actual_state)} onClick={() => void mutate(() => tga3Api.pauseAgent(taskId, agent.agent_id), `${agent.display_name} 已暂停`)}><CirclePause size={16} />暂停 Worker</button> : null}
    <p className="semantic-note">{agent.role === "worker" ? "暂停会中断当前 SDK 周期并保留会话；恢复后从同一上下文继续。" : "状态来自后端运行记录，不由前端推算。"}</p>
  </div>;
}

function Dialogue({ taskId, agent, messages, pendingQuestion, busy, mutate }: {
  taskId: string; agent: AgentRun; messages: DialogueMessage[]; pendingQuestion?: DialogueMessage; busy: boolean;
  mutate: (action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const [textValue, setTextValue] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const filtered = useMemo(() => messages.filter((message) =>
    message.channel_agent_id === agent.agent_id
    || String(message.payload.agent_id ?? "") === agent.agent_id
    || (agent.agent_id === "supervisor" && message.kind === "question")), [agent.agent_id, messages]);
  useEffect(() => endRef.current?.scrollIntoView({ block: "end" }), [filtered.length]);
  const isPendingChannel = agent.agent_id === "supervisor" && pendingQuestion;
  const send = async (event: FormEvent) => {
    event.preventDefault();
    const value = textValue.trim();
    if (isPendingChannel ? !value : !value && !files.length) return;
    await mutate(async () => {
      const uploaded = await Promise.all(files.map((file) => tga3Api.uploadFile(taskId, file)));
      const attachmentIds = uploaded.map((item) => item.file.id);
      if (isPendingChannel) await tga3Api.answerQuestion(String(pendingQuestion.payload.question_id), value, attachmentIds);
      else await tga3Api.addPrompt(taskId, value || "请读取本次追加的文件。", agent.role === "worker" ? [agent.agent_id] : [], attachmentIds);
      setTextValue("");
      setFiles([]);
    }, isPendingChannel ? "回答已写入共享 Q&A" : "提示已写入黑板");
  };
  return <div className="dialogue-pane">
    <div className="dialogue-caption"><MessageSquareText size={15} /><span>过程摘要与动作</span><small>不展示隐藏推理链</small></div>
    <div className="message-list">{filtered.length ? filtered.map((message) => <Message message={message} key={message.id} />) : <div className="conversation-empty"><Bot size={24} /><p>该 Agent 还没有对话事件。</p></div>}<div ref={endRef} /></div>
    {isPendingChannel ? <div className="pending-question"><Lightbulb size={16} /><span><b>Supervisor 等待你的回答</b><p>{pendingQuestion.text}</p></span></div> : null}
    {agent.role !== "reporter" ? <form className="message-composer" onSubmit={(event) => void send(event)}>
      {files.length ? <div className="composer-files">{files.map((file) => <span key={`${file.name}-${file.size}`}>{file.name}</span>)}</div> : null}
      <textarea value={textValue} onChange={(event) => setTextValue(event.target.value)} rows={3} placeholder={isPendingChannel ? "回答这个问题……" : `给 ${agent.display_name} 追加提示……`} />
      <footer><label className="attach-button"><FileUp size={16} /><span>文件</span><input type="file" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []))} /></label><button className="send-button" disabled={busy || (isPendingChannel ? !textValue.trim() : !textValue.trim() && !files.length)}>{busy ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}</button></footer>
    </form> : <p className="semantic-note">Reporter 只在最终候选出现后工作，不接收运行期提示。</p>}
  </div>;
}

function Message({ message }: { message: DialogueMessage }) {
  const own = message.actor.role === "user";
  return <article className={`dialogue-message ${own ? "from-user" : "from-agent"} message-${message.kind}`}><header><b>{message.actor.display_name}</b><small>#{message.seq} · {formatTime(message.created_at)}</small></header><p>{message.text}</p><span>{dialogueLabel(message.kind)}</span></article>;
}

function AgentBlackboard({ entries }: { entries: BlackboardEntry[] }) {
  return <div className="agent-board">{entries.length ? [...entries].reverse().map((entry) => <article key={entry.id}><header><b>{kindLabel(entry.kind)}</b><small>#{entry.seq}</small></header><EntryBody entry={entry} /></article>) : <div className="conversation-empty"><ShieldCheck /><p>共享黑板尚无条目。</p></div>}</div>;
}

function AgentSkills({ skills }: { skills: SkillInfo[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  const document = useQuery({ queryKey: ["tga3", "skill", selected], queryFn: () => tga3Api.skill(selected!), enabled: Boolean(selected) });
  return <div className="agent-skills"><p className="semantic-note">所有 Agent 看到同一份名称目录，并自行决定是否读取。</p>{skills.map((skill) => <button onClick={() => setSelected(selected === skill.name ? null : skill.name)} key={skill.name}><Boxes size={15} /><span><b>{skill.name}</b><small>{skill.description}</small></span></button>)}{selected ? <pre>{document.data?.content ?? "正在读取…"}</pre> : null}</div>;
}

function Info({ title, value }: { title: string; value: string }) { return <div className="info-row"><small>{title}</small><b>{value}</b></div>; }
function text(value: unknown) { return typeof value === "string" ? value : value == null ? "" : JSON.stringify(value); }
function avatar(agent: AgentRun) { return agent.display_name.slice(0, 1).toUpperCase(); }
function kindLabel(kind: string) { return ({ user_prompt: "提示", user_file: "用户文件", supervisor_advice: "Supervisor 建议", finding: "Finding", qa: "Q&A", final_candidate: "最终候选" } as Record<string, string>)[kind] ?? kind; }
function entryIcon(kind: string) { return kind === "finding" ? <CheckCircle2 size={15} /> : kind === "final_candidate" ? <Flag size={15} /> : kind === "supervisor_advice" ? <Lightbulb size={15} /> : kind === "qa" ? <MessageSquareText size={15} /> : kind === "user_prompt" ? <UserRound size={15} /> : <FileUp size={15} />; }
function dialogueLabel(kind: string) { return ({ assistant_delta: "过程摘要", blackboard_progress: "黑板进度", agent_status: "状态", action_started: "动作开始", action_completed: "动作完成", question: "提问", user_message: "用户提示", model_changed: "模型切换", error: "错误", system: "系统" } as Record<string, string>)[kind] ?? kind; }
