import { useMemo, useState } from "react";
import { ExternalLink, FileSearch, ShieldCheck, Sparkles, TerminalSquare } from "lucide-react";
import { runtimeApi } from "../../runtime/api-v2";
import type { RuntimeAction, RuntimeEvent, RuntimeSnapshot, StrategyStatus } from "../../runtime/event-types";

type Turn = { number: number; events: RuntimeEvent[] };

const eventLabels: Record<string, string> = {
  CONTEXT_BUILT: "上下文已构建", MESSAGE_START: "请求模型 Provider", MESSAGE_END: "模型返回决策",
  MANAGER_DECISION: "治理层决策", TOOL_EXECUTION_START: "工具开始执行", TOOL_EXECUTION_END: "工具执行结束",
  STRATEGY_STEP_UPDATED: "策略步骤更新", OBSERVER_TRIGGERED: "Observer 已触发", OBSERVER_DIRECTIVE: "Observer 建议",
  FINISH_ATTEMPTED: "完成校验尝试", FINISH_REJECTED: "完成校验拒绝", FINISH_ACCEPTED: "完成校验通过",
  AGENT_TURN_ENDED: "本回合结束", CONTINUATION_TRIGGERED: "继续下一回合", AGENT_ERROR: "运行异常", ARTIFACT_SAVED: "Artifact 已保存",
};
const statusLabels: Record<string, string> = { created: "已创建", running: "运行中", paused: "已暂停", awaiting_approval: "等待审批", completed: "已完成", blocked: "已阻塞", cancelled: "已取消", failed: "失败", pending: "待执行", proposed: "待确认", pending_approval: "等待审批", approved: "已批准", rejected: "已拒绝", testing: "验证中", succeeded: "成功" };

export function ReactWorkbench({ snapshot }: { snapshot: RuntimeSnapshot }) {
  const turns = useMemo(() => groupTurns(snapshot.events), [snapshot.events]);
  return <main className="react-workbench">
    <StrategyPanel snapshot={snapshot} />
    <section className="react-timeline" aria-label="ReAct 回合时间线">
      <header><div><span>REACT TIMELINE</span><h2>模型决策与真实执行</h2></div><small>{turns.length} 个已记录回合</small></header>
      {turns.map((turn) => <TurnCard key={turn.number} turn={turn} snapshot={snapshot} />)}
      {!turns.length ? <Empty title="等待首个回合" detail="Session 启动后，模型请求、工具调用和治理决策会按顺序显示。" /> : null}
    </section>
    <EvidencePanel snapshot={snapshot} />
  </main>;
}

function StrategyPanel({ snapshot }: { snapshot: RuntimeSnapshot }) {
  const cards = snapshot.runtime.strategy_cards;
  const memory = snapshot.runtime.memory;
  return <aside className="react-side-panel strategy-panel">
    <header><Sparkles size={16} /><div><span>STRATEGY</span><h2>策略与记忆</h2></div></header>
    <div className="react-panel-scroll">
      {cards.map((card) => <article className="strategy-card" key={card.id}>
        <div className="strategy-card-title"><b>{card.title}</b><State value={card.status} /></div>
        <p>{card.summary}</p>
        <ol>{card.steps.map((step) => <li className={step.id === card.active_step_id ? "active" : ""} key={step.id}>
          <div><span>{step.id === card.active_step_id ? "当前" : "步骤"}</span><State value={step.status} /></div>
          <b>{step.title}</b><small>{step.success_marker || step.expected_request || "等待证据"}</small>
          {step.last_result ? <p>{step.last_result}</p> : null}
          <ArtifactLinks taskId={snapshot.task.id} ids={step.evidence_artifact_ids} />
        </li>)}</ol>
      </article>)}
      {!cards.length ? <Empty title="暂无 StrategyCard" detail="任务启动时将从目标或用户提示生成首张策略卡。" /> : null}
      <section className="memory-ledger"><h3>EvidenceMemory</h3>{memory.slice().reverse().slice(0, 12).map((item) => <article key={item.id}><State value={item.kind} /><p>{item.content}</p><ArtifactLinks taskId={snapshot.task.id} ids={item.artifact_ids} /></article>)}{!memory.length ? <small>暂无持久记忆。</small> : null}</section>
    </div>
  </aside>;
}

function TurnCard({ turn, snapshot }: { turn: Turn; snapshot: RuntimeSnapshot }) {
  const [open, setOpen] = useState(turn.number === snapshot.session.turn_count);
  const metric = snapshot.context_metrics?.find((item) => item.turn === turn.number);
  const usage = turn.events.find((event) => event.type === "PROVIDER_USAGE")?.payload;
  const rejected = turn.events.find((event) => event.type === "FINISH_REJECTED");
  return <article className={`react-turn ${rejected ? "rejected" : ""}`} data-testid="react-turn">
    <button className="react-turn-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <span>TURN {String(turn.number).padStart(2, "0")}</span><b>{turnTitle(turn.events)}</b>
      <small>{metric ? `${metric.working_chars.toLocaleString()} chars` : "上下文待记录"} · {turn.events.length} events</small><i>{open ? "−" : "+"}</i>
    </button>
    {open ? <div className="react-turn-body">
      <div className="turn-metrics"><span>Context <b>{metric?.working_chars.toLocaleString() ?? "-"}</b></span><span>Input tokens <b>{String(usage?.input_tokens ?? metric?.provider_input_tokens ?? "-")}</b></span><span>Output tokens <b>{String(usage?.output_tokens ?? metric?.provider_output_tokens ?? "-")}</b></span><span>Provider <b>{usage?.duration_ms != null ? `${usage.duration_ms} ms` : "-"}</b></span></div>
      {turn.events.map((event) => <EventRow key={event.seq} event={event} snapshot={snapshot} />)}
    </div> : null}
  </article>;
}

function EventRow({ event, snapshot }: { event: RuntimeEvent; snapshot: RuntimeSnapshot }) {
  const action = event.payload.action_id ? snapshot.actions.find((item) => item.id === event.payload.action_id) : undefined;
  const isTool = event.type.startsWith("TOOL_EXECUTION_");
  const isRejected = event.type === "FINISH_REJECTED" || event.payload.decision === "denied";
  return <div className={`react-event ${isTool ? "tool" : ""} ${isRejected ? "rejected" : ""}`} data-testid={isTool ? "tool-event" : undefined}>
    <div className="event-rail"><span>{event.seq}</span><i /></div>
    <div className="event-content">
      <header><b>{eventLabels[event.type] ?? readableType(event.type)}</b><time>{formatTime(event.created_at)}</time></header>
      <p>{eventSummary(event, action)}</p>
      {isTool ? <ToolFacts event={event} action={action} taskId={snapshot.task.id} /> : null}
      {event.type === "FINISH_REJECTED" && event.payload.missing?.length ? <ul className="missing-list">{event.payload.missing.map((item) => <li key={item}>{item}</li>)}</ul> : null}
      {event.type === "MESSAGE_END" && event.payload.content ? <blockquote>{String(event.payload.content)}</blockquote> : null}
    </div>
  </div>;
}

function ToolFacts({ event, action, taskId }: { event: RuntimeEvent; action?: RuntimeAction; taskId: string }) {
  const approved = action?.authorization?.allowed !== false && event.payload.decision !== "denied";
  const artifactIds = event.payload.artifact_ids ?? event.payload.artifacts?.map((item) => item.artifact_id) ?? action?.artifact_ids ?? [];
  const errorCode = event.payload.error?.code ?? action?.error?.code;
  const resultSummary = event.payload.summary ?? action?.summary;
  return <div className="tool-facts">
    <span><small>提出者</small><b>配置的模型</b></span><span><small>批准者</small><b>{approved ? "TGA 治理层" : "治理拒绝"}</b></span>
    <span data-testid="execution-location"><small>执行位置</small><b>{event.payload.execution_location ?? executionLocation(event, action)}</b></span>
    <span><small>状态</small><b>{statusLabels[String(event.payload.status ?? action?.status)] ?? String(event.payload.status ?? action?.status ?? "等待")}</b></span>
    <span><small>风险</small><b>{action?.risk ?? "未声明"}</b></span><span><small>耗时</small><b>{event.payload.duration_ms != null ? `${event.payload.duration_ms} ms` : "-"}</b></span>
    <span><small>结果保存</small><b>{artifactIds.length ? "Artifact Store" : "无 Artifact"}</b></span>
    <span><small>错误码</small><b>{errorCode ?? "-"}</b></span>
    {action?.rationale ? <p><small>Rationale</small>{action.rationale}</p> : null}{action?.expected_outcome ? <p><small>Expected outcome</small>{action.expected_outcome}</p> : null}
    {action?.arguments && Object.keys(action.arguments).length ? <p><small>脱敏参数</small><code>{boundedJson(action.arguments)}</code></p> : null}
    {resultSummary ? <p><small>回传模型</small>{String(resultSummary)}</p> : null}
    <ArtifactLinks taskId={taskId} ids={artifactIds} />
  </div>;
}

function EvidencePanel({ snapshot }: { snapshot: RuntimeSnapshot }) {
  const [tab, setTab] = useState<"artifacts" | "result">("artifacts");
  const accepted = snapshot.events.some((event) => event.type === "FINISH_ACCEPTED");
  const finished = [...snapshot.events].reverse().find((event) => event.type === "AGENT_FINISHED");
  const confirmed = accepted && Boolean(finished);
  return <aside className="react-side-panel evidence-panel">
    <header><FileSearch size={16} /><div><span>EVIDENCE</span><h2>证据与结果</h2></div></header>
    <nav><button className={tab === "artifacts" ? "active" : ""} onClick={() => setTab("artifacts")}>Artifacts {snapshot.artifacts.length}</button><button className={tab === "result" ? "active" : ""} onClick={() => setTab("result")}>最终结果</button></nav>
    <div className="react-panel-scroll">
      {tab === "artifacts" ? snapshot.artifacts.slice().reverse().map((artifact) => { const index = snapshot.artifact_indexes?.find((item) => item.artifact_id === artifact.id); return <article className="evidence-card" key={artifact.id}>
        <div><State value={artifact.kind} /><span className="artifact-actions"><a href={runtimeApi.artifactUrl(snapshot.task.id, artifact.id)} target="_blank" rel="noreferrer"><ExternalLink size={13} />预览</a><a href={runtimeApi.artifactDownloadUrl(snapshot.task.id, artifact.id)} target="_blank" rel="noreferrer">下载</a></span></div>
        <b>{artifact.tool ?? artifact.kind}</b><p>{artifact.target ?? artifact.path}</p>
        <dl><div><dt>Created</dt><dd>{artifact.created_at ? formatTime(artifact.created_at) : "-"}</dd></div><div><dt>SHA256</dt><dd>{artifact.sha256 ? `${artifact.sha256.slice(0, 16)}…` : "-"}</dd></div><div><dt>Input</dt><dd>{artifact.input_id ?? "-"}</dd></div><div><dt>截断</dt><dd>{artifact.truncated ? "已截断" : "完整"}</dd></div><div><dt>Provenance</dt><dd>{provenanceSummary(artifact.provenance)}</dd></div>{index ? <div><dt>Index</dt><dd>{index.summary || index.document_type} · {index.segment_count} segments{index.source_refs.length ? ` · ${index.source_refs.slice(0, 3).join(", ")}` : ""}</dd></div> : null}</dl>
      </article>; }) : null}
      {tab === "artifacts" && !snapshot.artifacts.length ? <Empty title="暂无证据" detail="工具输出持久化后会显示在这里。" /> : null}
      {tab === "result" ? <FinalResult snapshot={snapshot} confirmed={confirmed} event={finished} /> : null}
    </div>
  </aside>;
}

function FinalResult({ snapshot, confirmed, event }: { snapshot: RuntimeSnapshot; confirmed: boolean; event?: RuntimeEvent }) {
  if (!confirmed) return <Empty title={snapshot.session.status === "running" ? "尚未确认最终结果" : statusLabels[snapshot.session.status] ?? snapshot.session.status} detail={snapshot.session.stop_reason || "只有 FINISH_ACCEPTED 与 AGENT_FINISHED 同时存在时才展示确认结果。"} />;
  return <article className="final-result" data-testid="final-result"><ShieldCheck size={28} /><span>已确认最终结果</span><h3>{event?.payload.summary ?? "任务完成"}</h3>
    {event?.payload.coverage?.length ? <section><b>覆盖范围</b><ul>{event.payload.coverage.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
    {event?.payload.limitations?.length ? <section><b>限制</b><ul>{event.payload.limitations.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
    {snapshot.flags.map((flag) => <a key={flag.value} href={runtimeApi.artifactUrl(snapshot.task.id, flag.evidence_artifact_id)} target="_blank" rel="noreferrer"><code>{flag.value}</code><ExternalLink size={13} /></a>)}
    <ArtifactLinks taskId={snapshot.task.id} ids={event?.payload.evidence_artifact_ids ?? []} /><small>stop_reason: {snapshot.session.stop_reason || "finish_accepted"}</small>
  </article>;
}

function ArtifactLinks({ taskId, ids }: { taskId: string; ids: string[] }) { const unique = [...new Set(ids)]; return unique.length ? <div className="artifact-links">{unique.map((id) => <a key={id} href={runtimeApi.artifactUrl(taskId, id)} target="_blank" rel="noreferrer">{id}<ExternalLink size={11} /></a>)}</div> : null; }
function State({ value }: { value: string }) { return <span className={`runtime-state ${value}`}>{statusLabels[value] ?? value}</span>; }
function Empty({ title, detail }: { title: string; detail: string }) { return <div className="react-empty"><TerminalSquare size={22} /><b>{title}</b><p>{detail}</p></div>; }

function groupTurns(events: RuntimeEvent[]): Turn[] {
  const groups = new Map<number, RuntimeEvent[]>(); let current = 0;
  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    const declared = typeof event.payload.turn === "number" ? event.payload.turn : undefined;
    if (event.type === "MESSAGE_START") current = declared ?? current + 1;
    const turn = declared ?? current;
    if (turn <= 0 || ["SESSION_STARTED", "SESSION_CONTROLLED", "SESSION_STOPPED", "AGENT_STARTED"].includes(event.type)) continue;
    groups.set(turn, [...(groups.get(turn) ?? []), event]);
  }
  return [...groups].map(([number, grouped]) => ({ number, events: grouped }));
}
function turnTitle(events: RuntimeEvent[]) { if (events.some((event) => event.type === "FINISH_ACCEPTED")) return "完成校验已通过"; if (events.some((event) => event.type === "FINISH_REJECTED")) return "完成条件不足，继续执行"; const tool = events.find((event) => event.type === "TOOL_EXECUTION_START")?.payload.tool_name; return tool ? `执行 ${tool}` : "模型推进任务"; }
function eventSummary(event: RuntimeEvent, action?: RuntimeAction) { if (event.type === "MESSAGE_START") return "已冻结本轮 MCP Catalog 快照，并向配置的模型 Provider 发送工具协议请求。"; if (event.type === "MANAGER_DECISION") return event.payload.decision === "denied" ? `治理拒绝：${String(event.payload.reason ?? "不满足授权策略")}` : "策略、范围、风险和预算检查已通过。"; if (event.type === "TOOL_EXECUTION_START") return `${event.payload.tool_name ?? action?.capability ?? "工具"} 正在真实执行。`; if (event.type === "TOOL_EXECUTION_END") return event.payload.summary ?? action?.summary ?? `执行状态：${event.payload.status ?? "未知"}`; if (event.type === "FINISH_REJECTED") return `完成请求未通过：${event.payload.validator_code ?? "缺少必要条件"}。Session 保持运行。`; if (event.type === "FINISH_ACCEPTED") return event.payload.summary ?? "完成校验通过。"; if (event.type === "OBSERVER_DIRECTIVE") return String(event.payload.strategy_advice ?? "Observer 提供了下一步策略建议。"); return String(event.payload.summary ?? event.payload.reason ?? event.payload.message ?? event.payload.status ?? "运行时事件已记录"); }
function executionLocation(event: RuntimeEvent, action?: RuntimeAction) { if (event.payload.tool_kind === "mcp") return "Remote MCP Service"; if (action?.capability.startsWith("workspace.")) return "Session Workspace"; if (action?.capability === "http.request") return "Authorized HTTP Target"; if (event.payload.tool_name?.startsWith("input_")) return "Input Store"; return "TGA Process"; }
function readableType(type: string) { return type.split("_").map((part) => part.charAt(0) + part.slice(1).toLowerCase()).join(" "); }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString("zh-CN", { hour12: false }); }
function provenanceSummary(value?: Record<string, unknown>) { const entries = Object.entries(value ?? {}).filter(([, item]) => item != null && typeof item !== "object").slice(0, 4); return entries.length ? entries.map(([key, item]) => `${key}=${String(item)}`).join(" · ") : "task-owned"; }
function boundedJson(value: Record<string, unknown>) { const encoded = JSON.stringify(value); return encoded.length > 600 ? `${encoded.slice(0, 600)}...` : encoded; }
export function RuntimeLoading({ error, onRetry }: { error: string | null; onRetry: () => void }) { return <div className="runtime-loading"><TerminalSquare size={28} /><b>{error ? "运行时加载失败" : "正在读取 Session"}</b><p>{error ?? "正在加载初始 Snapshot 并连接增量事件流。"}</p>{error ? <button onClick={onRetry}>重试</button> : null}</div>; }
export function redact(value: string) { return value.replace(/(authorization|cookie|token|secret|api[_-]?key|password)\s*[:=]\s*[^\s,;]+/gi, "$1=[REDACTED]"); }
