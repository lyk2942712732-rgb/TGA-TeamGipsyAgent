import { useMemo, useState } from "react";
import { selectEventsBySolver } from "../models/selectors";
import type { RuntimeEvent, RuntimeSolver, RuntimeStore } from "../models/types";
import { SkillSummary } from "../../skills/SkillSummary";
import { StatusBadge } from "../../../shared/StatusBadge";

type InspectorTab = "overview" | "transcript" | "plan" | "knowledge" | "skills" | "tools" | "artifacts" | "config";
const TABS: Array<[InspectorTab, string]> = [["overview", "概览"], ["transcript", "Transcript"], ["plan", "Local Plan"], ["knowledge", "Knowledge"], ["skills", "Skills"], ["tools", "Tools"], ["artifacts", "Artifacts"], ["config", "配置"]];

export function SolverInspector({ solver, store }: { solver: RuntimeSolver | null; store?: RuntimeStore }) {
  const [tab, setTab] = useState<InspectorTab>("overview");
  return <aside className="solver-inspector" aria-label="Solver 检查器"><header><span>INSPECTOR</span><h2>Solver 检查器</h2></header>{solver ? <><div className="solver-inspector-title"><div><h3>{solver.solverId}</h3><small>{solver.definitionId}</small></div><StatusBadge value={solver.status} /></div><div className="solver-inspector-tabs" role="tablist" aria-label="Solver 检查器标签">{TABS.map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>)}</div><div className="solver-inspector-panel" role="tabpanel">{tab === "overview" ? <Overview solver={solver} /> : null}{tab === "transcript" ? <Transcript solver={solver} store={store} /> : null}{tab === "plan" ? <LocalPlan solver={solver} store={store} /> : null}{tab === "knowledge" ? <Knowledge solver={solver} store={store} /> : null}{tab === "skills" ? <Skills solver={solver} store={store} /> : null}{tab === "tools" ? <Tools solver={solver} store={store} /> : null}{tab === "artifacts" ? <Artifacts solver={solver} store={store} /> : null}{tab === "config" ? <Config solver={solver} /> : null}</div></> : <p className="runtime-empty">选择一个 Solver 查看详情</p>}</aside>;
}

function Overview({ solver }: { solver: RuntimeSolver }) { return <><p>{solver.currentSummary || "当前没有摘要"}</p><dl className="solver-summary-list"><Item label="角色" value={solver.orchestrationRole} /><Item label="专业方向" value={solver.specialties.join(" / ") || "通用"} /><Item label="父 Solver" value={solver.parentSolverId ?? "无"} /><Item label="当前 Intent" value={solver.assignedIntentId ?? "未分配"} />{Object.entries(solver.budgetUsage).map(([key, value]) => <Item key={key} label={key} value={String(value)} />)}</dl><SkillSummary solver={solver} /></>; }

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

function LocalPlan({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) { const intent = solver.assignedIntentId && store ? store.intentsById[solver.assignedIntentId] : undefined; return <section><h4>Local Plan</h4>{intent ? <dl className="solver-summary-list"><Item label="Intent" value={intent.title} /><Item label="目标" value={intent.objective} /><Item label="状态" value={intent.status} /><Item label="依赖" value={intent.dependencies.join("、") || "无"} /></dl> : <p className="runtime-empty">后端未投影该 Solver 的 Local Plan 正文</p>}</section>; }

function Knowledge({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) {
  const values = store ? Object.values(store.knowledgeById) : [];
  const groups: Array<[string, typeof values]> = [["Solver Candidate", values.filter((item) => item.scope === "solver" && item.targetId === solver.solverId && item.status === "candidate")], ["Intent Shared", values.filter((item) => item.scope === "intent" && item.targetId === solver.assignedIntentId && !["rejected", "superseded"].includes(item.status))], ["Task Verified", values.filter((item) => item.scope === "task" && item.status === "verified")], ["Rejected / Superseded", values.filter((item) => ["rejected", "superseded"].includes(item.status))]];
  return <div className="inspector-knowledge">{groups.map(([label, items]) => <section key={label}><h4>{label}</h4>{items.length ? items.map((item) => <p key={item.knowledgeId}>{item.contentPreview}</p>) : <small>暂无</small>}</section>)}</div>;
}

function Skills({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) { const taskBundle = record(store?.taskCommonSkillSnapshot); const common = list(taskBundle.skills); const names = list(solver.skillSnapshot.names); return <div className="inspector-skills"><section><h4>Task Common Skills</h4>{common.length ? common.map((item, index) => <pre key={index}>{JSON.stringify(item, null, 2)}</pre>) : <small>未投影版本/hash/选择原因</small>}</section><section><h4>Solver Specialized Skills</h4>{names.length ? names.map(String).map((name) => <p key={name}>{name}</p>) : <small>未选择</small>}<dl className="solver-summary-list"><Item label="selector" value={String(solver.skillSnapshot.selector ?? "未投影")} /><Item label="count" value={String(solver.skillSnapshot.count ?? 0)} /></dl></section></div>; }

function Tools({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) { const allowed = list(solver.toolPolicySummary.allowed_capabilities).map(String); const events = store ? selectEventsBySolver(store, solver.solverId) : []; const errors = events.filter((event) => event.type.includes("FAILED") || event.payload.error).length; return <section><h4>Tools</h4><dl className="solver-summary-list"><Item label="允许" value={allowed.join("、") || "未投影"} /><Item label="禁止" value="未授权能力默认不可见/不可用" /><Item label="风险" value={String(solver.toolPolicySummary.profile ?? "由后端策略控制")} /><Item label="调用" value={`调用 ${solver.budgetUsage.tool_calls ?? 0} 次`} /><Item label="限速" value={String(solver.toolPolicySummary.rate_limit ?? "未投影")} /><Item label="错误" value={`${errors} 次`} /></dl></section>; }

function Artifacts({ solver, store }: { solver: RuntimeSolver; store?: RuntimeStore }) { const values = store ? Object.values(store.artifactsById).filter((item) => item.intentId === solver.assignedIntentId) : []; return <section><h4>已发布 Artifacts</h4>{values.length ? values.map((item) => <article key={item.artifactId}><b>{item.artifactId}</b><small>{item.kind} · {item.sha256}</small></article>) : <p className="runtime-empty">没有已发布产物</p>}</section>; }
function Config({ solver }: { solver: RuntimeSolver }) { return <section><h4>冻结配置</h4><pre>{JSON.stringify({ definition_id: solver.definitionId, model_snapshot: solver.modelSnapshot, skill_snapshot: solver.skillSnapshot, tool_policy: solver.toolPolicySummary, timestamps: solver.timestamps }, null, 2)}</pre></section>; }
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
