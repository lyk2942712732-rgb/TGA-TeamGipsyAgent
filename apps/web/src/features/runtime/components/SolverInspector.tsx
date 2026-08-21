import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, Paperclip, Pause, Play, Send, ScrollText, UserRound, Users, X } from "lucide-react";
import { selectEventsBySolver } from "../models/selectors";
import type { RuntimeEvent, RuntimeSolver, RuntimeStore } from "../models/types";
import { StatusBadge } from "../../../shared/StatusBadge";
import { runtimeApi } from "../../../runtime/api-v2";
import { deleteStagedInput, fetchAgentModelOptions, stageInput, type AgentModelOptions, type ProviderProtocol, type StagedAsset } from "../../../api/tasks";
import type { TaskMode } from "../../../modes";

/**
 * Reference image 05's Solver Inspector: a labelled summary of the selected
 * Solver (当前阶段 / 所属 Solver / 工具 / 预算 / Token / 状态 / 关联对象) with a
 * 查看详细日志 button underneath.  The reference stops there; the tab strip below
 * the summary keeps the drill-downs the workbench already projects.
 */

type InspectorTab = "overview" | "chat" | "transcript" | "plan" | "skills" | "tools" | "artifacts" | "config";
const TABS: Array<[InspectorTab, string]> = [["overview", "概览"], ["chat", "对话"], ["transcript", "事件日志"], ["plan", "当前 Intent"], ["skills", "Skills"], ["tools", "Tools"], ["artifacts", "Artifacts"], ["config", "配置"]];

export function SolverInspector({ solver, store, readonly = false, chatOpenNonce = 0, onChanged = () => undefined }: { solver: RuntimeSolver | null; store?: RuntimeStore; readonly?: boolean; chatOpenNonce?: number; onChanged?: () => void }) {
  const [tab, setTab] = useState<InspectorTab>("overview");
  useEffect(() => { if (chatOpenNonce) setTab("chat"); }, [chatOpenNonce]);
  return <aside className="solver-inspector" aria-label="Solver 检查器">
    <header><h2>Solver Inspector</h2><Users size={16} aria-hidden="true" /></header>
    {solver ? <>
      <div className="solver-inspector-title">
        <div><h3>{solver.solverId}</h3><small>{solver.definitionId}</small></div>
        <StatusBadge value={solver.status} />
      </div>
      <div className="solver-inspector-tabs" role="tablist" aria-label="Solver 检查器标签">
        {TABS.map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>)}
      </div>
      <div className="solver-inspector-panel" role="tabpanel">
        {tab === "overview" ? <Overview solver={solver} store={store} /> : null}
        {tab === "chat" && store ? <SolverChat solver={solver} store={store} readonly={readonly} onChanged={onChanged} /> : null}
        {tab === "transcript" ? <Transcript solver={solver} store={store} /> : null}
        {tab === "plan" ? <LocalPlan solver={solver} store={store} /> : null}
        {tab === "skills" ? <Skills solver={solver} store={store} /> : null}
        {tab === "tools" ? <Tools solver={solver} store={store} /> : null}
        {tab === "artifacts" ? <Artifacts solver={solver} store={store} /> : null}
        {tab === "config" ? <Config solver={solver} /> : null}
      </div>
      <footer>
        <button type="button" className="ref-secondary-button" onClick={() => setTab("transcript")}>
          <ScrollText size={14} aria-hidden="true" />查看详细日志
        </button>
      </footer>
    </> : <p className="runtime-empty">选择一个 Solver 查看详情</p>}
  </aside>;
}

function Overview({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) {
  const intent = solver.assignedIntentId && store ? store.intentsById[solver.assignedIntentId] : undefined;
  const kaliBinding = record(solver.capabilityBinding.kali);
  const tools = [...list(solver.capabilityBinding.host_capability_ids).map(String), ...list(kaliBinding.capabilities).map(String)];
  const tokens = Number(solver.budgetUsage.input_tokens ?? 0) + Number(solver.budgetUsage.output_tokens ?? 0);
  const turns = Number(solver.budgetUsage.turns ?? 0);
  const maxTurns = Number(store?.session.maxTurns ?? 0);
  return <div className="inspector-summary">
    <Block label="当前阶段">
      <div className="inspector-stage">
        <b>{intent?.title ?? solver.assignedIntentId ?? "未分配 Intent"}</b>
        <StatusBadge value={intent?.status ?? solver.status} />
      </div>
      <p>{solver.currentSummary || "当前没有摘要"}</p>
    </Block>

    <Block label="所属 Solver">
      <div className="inspector-owner">
        <span aria-hidden="true">{solver.solverId.replace(/^solver[_-]/, "").charAt(0).toUpperCase()}</span>
        <b>{solver.orchestrationRole} · {solver.specialties.join(" / ") || "通用"}</b>
      </div>
    </Block>

    <Block label="工具">
      {/* Capped at four: the rail is 268px tall-limited and the Tools tab lists
          the full allow-list one click away. */}
      {tools.length
        ? <ul className="inspector-tools">
          {tools.slice(0, 2).map((name) => <li key={name}>{name}</li>)}
          {tools.length > 2 ? <li className="inspector-more">另有 {tools.length - 2} 项</li> : null}
        </ul>
        : <p>未投影允许能力</p>}
    </Block>

    {/* 预算 and Token are one block: both are a single figure, and the rail has
        to fit seven sections without a scroller. */}
    <Block label="预算 / Token">
      <p>{turns} 回合{maxTurns ? `（上限 ${maxTurns}）` : ""} · {solver.budgetUsage.tool_calls ?? 0} 次工具调用</p>
      <b className="inspector-figure">{tokens.toLocaleString("en-US")} Token</b>
      {/* The per-Solver cap is not projected, so the bar tracks the task budget. */}
      <TokenBar used={tokens} total={Number(store?.session.taskBudgetUsage.input_tokens ?? 0) + Number(store?.session.taskBudgetUsage.output_tokens ?? 0)} />
    </Block>

    {/* 状态 is not repeated here — the panel header already carries the badge. */}
    <Block label="关联对象">
      {/* 定义 is already the sub-line of the panel title, and the Skill snapshot
          has its own tab — repeating either here only costs rail height. */}
      <dl className="solver-summary-list">
        <Item label="父 Solver" value={solver.parentSolverId ?? "无"} />
        <Item label="当前 Intent" value={solver.assignedIntentId ?? "未分配"} />
        <Item label="最后更新" value={String(solver.timestamps.updated_at ?? solver.timestamps.created_at ?? "—")} />
      </dl>
    </Block>
  </div>;
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return <section className="inspector-block"><h4>{label}</h4>{children}</section>;
}

function TokenBar({ used, total }: { used: number; total: number }) {
  if (!total) return null;
  const percent = Math.min(100, Math.round(used / total * 100));
  return <div className="inspector-bar"><i><em style={{ width: `${percent}%` }} /></i><small>{percent}%</small></div>;
}

type ConversationMessage = { id: number; role: "user" | "agent" | "action" | "system"; text: string; createdAt: string; attachments: Array<Record<string, unknown>> };

function SolverChat({ solver, store, readonly, onChanged }: { solver: RuntimeSolver; store: RuntimeStore; readonly: boolean; onChanged: () => void }) {
  const [text, setText] = useState("");
  const [assets, setAssets] = useState<StagedAsset[]>([]);
  const [models, setModels] = useState<AgentModelOptions["models"]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const events = useMemo(() => selectEventsBySolver(store, solver.solverId), [store, solver.solverId]);
  const messages = useMemo(() => events.map(conversationMessage).filter((value): value is ConversationMessage => value !== null), [events]);
  const latestControl = [...events].reverse().find((event) => event.type === "SOLVER_CONTROL_CHANGED");
  const paused = latestControl?.payload.state === "paused" || solver.status === "paused";
  const terminal = ["completed", "completed_with_limitations", "cancelled", "failed"].includes(store.session.status);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [messages.length]);
  useEffect(() => {
    let active = true;
    void fetchAgentModelOptions(store.task.mode as TaskMode).then((value) => {
      if (!active) return;
      const sdk = String(solver.modelSnapshot.sdk ?? "");
      const ready = value.models.filter((item) => item.ready && (sdk === "claude_agent" ? item.protocol === "anthropic" : item.protocol !== "anthropic"));
      setModels(ready);
      const provider = String(solver.modelSnapshot.provider_id ?? "");
      const model = String(solver.modelSnapshot.model_id ?? "");
      const protocol = String(solver.modelSnapshot.protocol ?? "");
      setSelectedModel(provider && model && protocol ? `${provider}::${model}::${protocol}` : ready[0] ? `${ready[0].provider_id}::${ready[0].model_id}::${ready[0].protocol}` : "");
    }).catch(() => { if (active) setModels([]); });
    return () => { active = false; };
  }, [solver.solverId, solver.modelSnapshot.provider_id, solver.modelSnapshot.model_id, solver.modelSnapshot.protocol, solver.modelSnapshot.sdk, store.task.mode]);

  async function addFiles(files: File[]) {
    setBusy("upload"); setNotice(null);
    try {
      const uploaded = await Promise.all(files.map((file) => stageInput(file)));
      setAssets((current) => [...current, ...uploaded]);
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : "附件上传失败"); }
    finally { setBusy(null); }
  }

  async function removeAsset(asset: StagedAsset) {
    setAssets((current) => current.filter((item) => item.id !== asset.id));
    if (asset.status === "uploaded") await deleteStagedInput(asset.id).catch(() => undefined);
  }

  async function send() {
    if ((!text.trim() && !assets.length) || busy || readonly || terminal) return;
    setBusy("send"); setNotice(null);
    try {
      await runtimeApi.solverMessage(store.task.id, solver.solverId, text.trim(), assets);
      setText(""); setAssets([]); setNotice("提示已进入该 Solver 的运行上下文"); onChanged();
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : "发送失败"); }
    finally { setBusy(null); }
  }

  async function togglePause() {
    setBusy("control"); setNotice(null);
    try {
      await runtimeApi.solverControl(store.task.id, solver.solverId, paused ? "resume" : "pause");
      setNotice(paused ? "Solver 已恢复" : "暂停请求已提交，将在下一个框架检查点生效"); onChanged();
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : "控制失败"); }
    finally { setBusy(null); }
  }

  async function changeModel(value: string) {
    setSelectedModel(value);
    const [providerId, modelId, protocol] = value.split("::");
    if (!providerId || !modelId || !protocol) return;
    setBusy("model"); setNotice(null);
    try {
      await runtimeApi.solverModel(store.task.id, solver.solverId, providerId, modelId, protocol as ProviderProtocol);
      setNotice("模型已切换，将用于该 Solver 的后续调用"); onChanged();
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : "模型切换失败"); }
    finally { setBusy(null); }
  }

  return <section className="solver-chat" aria-label={`${solver.solverId} 对话`}>
    <div className="solver-chat-controls">
      <label>后续模型<select value={selectedModel} disabled={readonly || busy !== null || !models.length} onChange={(event) => void changeModel(event.target.value)}>
        {!models.length ? <option value="">暂无已验证模型</option> : null}
        {models.map((item) => <option key={`${item.provider_id}::${item.model_id}::${item.protocol}`} value={`${item.provider_id}::${item.model_id}::${item.protocol}`}>{item.provider_name} / {item.model_name} / {item.protocol}</option>)}
      </select></label>
      <button type="button" disabled={readonly || busy !== null || terminal} onClick={() => void togglePause()}>{paused ? <Play size={13} /> : <Pause size={13} />}{paused ? "继续" : "暂停"}</button>
    </div>
    <p className="solver-chat-note">这里展示持久化的决策摘要、阶段和工具动作，不展示模型隐藏思维链。暂停在下一个 Agent 周期检查点生效。</p>
    <div className="solver-chat-thread" aria-live="polite">
      {messages.length ? messages.map((message) => <article key={message.id} data-role={message.role}>
        <span>{message.role === "user" ? <UserRound size={13} /> : <Bot size={13} />}</span>
        <div><small>{message.role === "user" ? "你" : message.role === "action" ? "动作" : solver.solverId} · {formatConversationTime(message.createdAt)}</small><p>{message.text}</p>{message.attachments.length ? <ul>{message.attachments.map((item, index) => <li key={`${message.id}-${index}`}>{String(item.name ?? item.path ?? "附件")}</li>)}</ul> : null}</div>
      </article>) : <p className="runtime-empty">还没有对话。Solver 的阶段、动作、提问和你的提示会出现在这里。</p>}
      <div ref={endRef} />
    </div>
    <div className="solver-chat-composer">
      {assets.length ? <div className="solver-chat-assets">{assets.map((asset) => <span key={asset.id}>{asset.originalName}<button aria-label={`移除 ${asset.originalName}`} onClick={() => void removeAsset(asset)}><X size={11} /></button></span>)}</div> : null}
      <textarea value={text} disabled={readonly || terminal} placeholder={store.session.status === "awaiting_user_input" && solver.solverId === "supervisor" ? "回答 Supervisor…" : `给 ${solver.solverId} 添加提示…`} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} />
      <div><input ref={fileRef} hidden multiple type="file" accept="image/*,audio/*,video/*,.pdf,.txt,.md,.json,.csv" onChange={(event) => { void addFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} /><button type="button" title="添加图片或文件" disabled={readonly || terminal || busy !== null} onClick={() => fileRef.current?.click()}><Paperclip size={14} /></button><small>支持图片、音视频和文档</small><button className="solver-chat-send" type="button" disabled={readonly || terminal || busy !== null || (!text.trim() && !assets.length)} onClick={() => void send()}><Send size={14} /></button></div>
    </div>
    {notice ? <p className="solver-chat-notice" role="status">{notice}</p> : null}
  </section>;
}

function conversationMessage(event: RuntimeEvent): ConversationMessage | null {
  const payload = event.payload;
  const attachments = Array.isArray(payload.attachments) ? payload.attachments.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
  if (event.type === "USER_SOLVER_MESSAGE") return { id: event.seq, role: "user", text: String(payload.content || (attachments.length ? "发送了附件" : "发送了提示")), createdAt: event.createdAt, attachments };
  if (event.type === "USER_INPUT_REQUIRED") return { id: event.seq, role: "agent", text: String(payload.question || payload.reason || "需要用户补充信息"), createdAt: event.createdAt, attachments: [] };
  if (["TOOL_ACTION_REQUESTED", "TOOL_COMPLETED", "ACTION_APPROVED", "ACTION_REJECTED", "SKILL_DOCUMENT_READ"].includes(event.type)) return { id: event.seq, role: "action", text: eventConversationSummary(event), createdAt: event.createdAt, attachments: [] };
  if (["SOLVER_CONTROL_CHANGED", "SOLVER_MODEL_CHANGED"].includes(event.type)) return { id: event.seq, role: "system", text: eventConversationSummary(event), createdAt: event.createdAt, attachments: [] };
  if (["SOLVER_STATUS_CHANGED", "PLAN_CREATED", "WORKER_ATTEMPT_COMPLETED", "REVIEW_COMPLETED", "SUPERVISOR_DECIDED", "REPORT_GENERATED"].includes(event.type)) return { id: event.seq, role: "agent", text: eventConversationSummary(event), createdAt: event.createdAt, attachments: [] };
  return null;
}

function eventConversationSummary(event: RuntimeEvent): string {
  const value = event.payload;
  return String(value.summary ?? value.question ?? value.feedback ?? value.reason ?? value.status ?? value.tool_name ?? event.type);
}

function formatConversationTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function Transcript({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) {
  const [mode, setMode] = useState<"concise" | "protocol">("concise");
  const [limit, setLimit] = useState(20);
  const [turn, setTurn] = useState("");
  const [toolCall, setToolCall] = useState("");
  const events = useMemo(() => store ? selectEventsBySolver(store, solver.solverId) : [], [store, solver.solverId]);
  const turns = [...new Set(events.map((event) => event.payload.turn).filter((value): value is number => typeof value === "number"))];
  const toolCalls = [...new Set(events.map((event) => event.payload.tool_call_id ?? event.payload.action_id).filter((value): value is string => typeof value === "string"))];
  const filtered = events.filter((event) => (!turn || event.payload.turn === Number(turn)) && (!toolCall || event.payload.tool_call_id === toolCall || event.payload.action_id === toolCall));
  const visible = filtered.slice(-limit);
  return <section className="solver-transcript" aria-label={`${solver.solverId} Transcript`}><div className="transcript-toolbar"><button aria-pressed={mode === "concise"} onClick={() => setMode("concise")}>简洁模式</button><button aria-pressed={mode === "protocol"} onClick={() => setMode("protocol")}>协议模式</button><label>回合<select value={turn} onChange={(event) => setTurn(event.target.value)}><option value="">全部</option>{turns.map((value) => <option key={value}>{value}</option>)}</select></label><label>Tool Call<select value={toolCall} onChange={(event) => setToolCall(event.target.value)}><option value="">全部</option>{toolCalls.map((value) => <option key={value}>{value}</option>)}</select></label></div><p>仅显示持久化事件中的模型决策与工具摘要，不展示隐藏思维链。</p>{visible.length ? <ol>{visible.map((event) => <li key={event.seq} id={`event-${event.seq}`}><header><a href={`#event-${event.seq}`}>#{event.seq}</a><b>{event.type}</b><small>{event.intentId ?? "Task"}</small></header>{mode === "protocol" ? <pre>{safePayload(event)}</pre> : <p>{eventSummary(event)}</p>}</li>)}</ol> : <p className="runtime-empty">该 Solver 暂无可回放事件；完整 Transcript 尚未由 API 投影。</p>}{filtered.length > visible.length ? <button onClick={() => setLimit((value) => value + 20)}>加载更早记录</button> : null}</section>;
}

function LocalPlan({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) {
  const intent = solver.assignedIntentId && store ? store.intentsById[solver.assignedIntentId] : undefined;
  const results = intent && store
    ? Object.values(store.workerResultsById).filter((item) => item.intentId === intent.intentId)
    : [];
  const result = results[results.length - 1];
  if (!intent) return <section><h4>Local Plan</h4><p className="runtime-empty">后端尚未投影该 Solver 的当前 Intent。</p></section>;
  const assessments = new Map<number, Record<string, unknown>>(
    (result?.criterionAssessments ?? []).map((item): [number, Record<string, unknown>] => [Number(item.criterion_index), item]),
  );
  return <section className="intent-acceptance-contract">
    <h4>Intent 验收契约</h4>
    <dl className="solver-summary-list">
      <Item label="Intent" value={intent.title} />
      <Item label="目标" value={intent.objective} />
      <Item label="状态" value={intent.status} />
      <Item label="依赖" value={intent.dependencies.join("、") || "无"} />
      {result ? <Item label="Worker 判定" value={result.completionStatus} /> : null}
    </dl>
    <AcceptanceList title="成功条件" values={intent.successCriteria} assessments={assessments} />
    <AcceptanceList title="预期证据" values={intent.expectedEvidence} />
    <AcceptanceList title="Runtime 允许工具" values={intent.allowedTools} />
    <AcceptanceList title="停止条件" values={intent.stopConditions} />
  </section>;
}

function AcceptanceList({ title, values, assessments }: { title: string; values: string[]; assessments?: Map<number, Record<string, unknown>> }) {
  return <section className="intent-acceptance-list"><h5>{title}<small>{values.length}</small></h5>{values.length ? <ol>{values.map((value, index) => { const assessment = assessments?.get(index); return <li key={`${index}-${value}`}><span>{value}</span>{assessment ? <small data-status={String(assessment.status ?? "unmet")}>{String(assessment.status ?? "unmet")} · {String(assessment.note ?? "")}</small> : null}</li>; })}</ol> : <p className="runtime-empty">未配置</p>}</section>;
}

function Skills({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) {
  const reads = (store ? selectEventsBySolver(store, solver.solverId) : []).filter((event) => event.type === "SKILL_DOCUMENT_READ");
  return <div className="inspector-skills"><section><h4>按需读取的 Skill 文档</h4><p>Skill 不再预先绑定任务或 Solver。这里仅显示该 Solver 实际读取过的文档。</p>{reads.length ? reads.map((event) => <article key={event.seq}><b>{String(event.payload.skill_name ?? "unknown")}</b><small>{String(event.payload.path ?? "SKILL.md")}</small><code>{String(event.payload.sha256 ?? "").slice(0, 16)}…</code></article>) : <small>尚未读取 Skill 文档</small>}</section></div>;
}

function Tools({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) {
  const kali = record(solver.capabilityBinding.kali);
  const host = list(solver.capabilityBinding.host_capability_ids).map(String);
  const kaliCapabilities = list(kali.capabilities).map(String);
  const events = store ? selectEventsBySolver(store, solver.solverId) : [];
  const errors = events.filter((event) => event.type.includes("FAILED") || event.payload.error).length;
  return <section><h4>Capabilities</h4><dl className="solver-summary-list"><Item label="Host" value={host.join(" / ") || "未投影"} /><Item label="Kali" value={kaliCapabilities.join(" / ") || "未启用"} /><Item label="Kali Profile" value={String(kali.profile_id ?? "未启用")} /><Item label="调用" value={`调用 ${solver.budgetUsage.tool_calls ?? 0} 次`} /><Item label="错误" value={`${errors} 次`} /></dl></section>;
}

function Artifacts({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) { const values = store ? Object.values(store.artifactsById).filter((item) => item.intentId === solver.assignedIntentId) : []; return <section><h4>已发布 Artifacts</h4>{values.length ? values.map((item) => <article key={item.artifactId}><b>{item.artifactId}</b><small>{item.kind} · {item.sha256}</small></article>) : <p className="runtime-empty">没有已发布产物</p>}</section>; }
function Config({ solver }: { solver: RuntimeSolver }) { return <section><h4>冻结配置</h4><pre>{JSON.stringify({ definition_id: solver.definitionId, model_snapshot: solver.modelSnapshot, capability_binding: solver.capabilityBinding, timestamps: solver.timestamps }, null, 2)}</pre></section>; }
function Item({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
function eventSummary(event: RuntimeEvent): string { return String(event.payload.summary ?? event.payload.reason ?? event.payload.status ?? event.payload.tool_name ?? "事件已记录"); }
function safePayload(event: RuntimeEvent): string { return JSON.stringify(sanitizeProtocolValue(event.payload), null, 2).slice(0, 8000); }
function sanitizeProtocolValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitizeProtocolValue);
  if (!value || typeof value !== "object") return value;
  const hiddenKeys = new Set(["reasoning", "reasoning_content", "chain_of_thought", "hidden_thoughts"]);
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .filter(([key]) => !hiddenKeys.has(key.toLowerCase()))
    .map(([key, child]) => [key, sanitizeProtocolValue(child)]));
}
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function list(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
