import { useMemo, useState } from "react";
import { Download, ExternalLink, FileSearch, ShieldCheck, Sparkles, TerminalSquare } from "lucide-react";
import { runtimeApi } from "../../runtime/api-v2";
import type { RuntimeAction, RuntimeEvent, RuntimeSnapshot, StrategyStatus } from "../../runtime/event-types";

type Turn = { number: number; events: RuntimeEvent[] };

const eventLabels: Record<string, string> = {
  CONTEXT_BUILT: "上下文已构建", MESSAGE_START: "请求模型 Provider", MESSAGE_END: "模型返回决策",
  MANAGER_DECISION: "治理层决策", TOOL_EXECUTION_START: "工具开始执行", TOOL_EXECUTION_END: "工具执行结束",
  STRATEGY_STEP_UPDATED: "策略步骤更新", OBSERVER_TRIGGERED: "Observer 已触发", OBSERVER_DIRECTIVE: "Observer 建议",
  FINISH_ATTEMPTED: "完成校验尝试", FINISH_REJECTED: "完成校验拒绝", FINISH_ACCEPTED: "完成校验通过",
  AGENT_TURN_ENDED: "本回合结束", CONTINUATION_TRIGGERED: "继续下一回合", AGENT_ERROR: "运行异常", ARTIFACT_SAVED: "证据产物已保存",
};
const statusLabels: Record<string, string> = { created: "已创建", running: "运行中", paused: "已暂停", awaiting_approval: "等待审批", completed: "已完成", blocked: "已阻塞", cancelled: "已取消", failed: "失败", pending: "待执行", proposed: "待确认", pending_approval: "等待审批", approved: "已批准", rejected: "已拒绝", testing: "验证中", succeeded: "成功" };

export function ReactWorkbench({ snapshot }: { snapshot: RuntimeSnapshot }) {
  const turns = useMemo(() => groupTurns(snapshot.events), [snapshot.events]);
  return <main className="react-workbench">
    <StrategyPanel snapshot={snapshot} />
    <section className="react-timeline" aria-label="ReAct 回合时间线">
      <header><div><span>执行时间线</span><h2>模型决策与真实执行</h2></div><small>{turns.length} 个已记录回合</small></header>
      {turns.map((turn) => <TurnCard key={turn.number} turn={turn} snapshot={snapshot} />)}
      {!turns.length ? <Empty title="等待首个回合" detail="任务启动后，模型请求、工具调用和治理决策会按顺序显示。" /> : null}
    </section>
    <EvidencePanel snapshot={snapshot} />
  </main>;
}

function StrategyPanel({ snapshot }: { snapshot: RuntimeSnapshot }) {
  const cards = snapshot.runtime.strategy_cards;
  const memory = snapshot.runtime.memory;
  return <aside className="react-side-panel strategy-panel">
    <header><Sparkles size={20} /><div><span>任务策略</span><h2>策略与记忆</h2></div></header>
    <div className="react-panel-scroll">
      {cards.map((card) => <article className="strategy-card" key={card.id}>
        <div className="strategy-card-title"><b>{localizeStrategyText(card.title)}</b><State value={card.status} /></div>
        <p>{localizeStrategyText(card.summary)}</p>
        <ol>{card.steps.map((step) => <li className={step.id === card.active_step_id ? "active" : ""} key={step.id}>
          <div><span>{step.id === card.active_step_id ? "当前" : "步骤"}</span><State value={step.status} /></div>
          <b>{localizeStrategyText(step.title)}</b><small>{localizeStrategyText(step.success_marker || step.expected_request || "等待证据")}</small>
          {step.last_result ? <p>{localizeRuntimeText(step.last_result)}</p> : null}
          <ArtifactLinks taskId={snapshot.task.id} ids={step.evidence_artifact_ids} />
        </li>)}</ol>
      </article>)}
      {!cards.length ? <Empty title="暂无候选策略" detail="任务启动时将从目标或用户提示生成首张策略卡。" /> : null}
      <section className="memory-ledger"><h3>证据记忆</h3>{memory.slice().reverse().slice(0, 12).map((item) => <article key={item.id}><State value={item.kind} /><p>{localizeRuntimeText(item.content)}</p><ArtifactLinks taskId={snapshot.task.id} ids={item.artifact_ids} /></article>)}{!memory.length ? <small>暂无持久记忆。</small> : null}</section>
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
      <span>第 {String(turn.number).padStart(2, "0")} 轮</span><b>{turnTitle(turn.events)}</b>
      <small>{metric ? `${metric.working_chars.toLocaleString()} 字符` : "上下文待记录"} · {turn.events.length} 个事件</small><i>{open ? "−" : "+"}</i>
    </button>
    {open ? <div className="react-turn-body">
      <div className="turn-metrics"><span>上下文 <b>{metric?.working_chars.toLocaleString() ?? "-"}</b></span><span>输入 Token <b>{String(usage?.input_tokens ?? metric?.provider_input_tokens ?? "-")}</b></span><span>输出 Token <b>{String(usage?.output_tokens ?? metric?.provider_output_tokens ?? "-")}</b></span><span>模型耗时 <b>{usage?.duration_ms != null ? `${usage.duration_ms} ms` : "-"}</b></span></div>
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
    <span data-testid="execution-location"><small>执行位置</small><b>{executionLocationLabel(String(event.payload.execution_location ?? executionLocation(event, action)))}</b></span>
    <span><small>状态</small><b>{statusLabels[String(event.payload.status ?? action?.status)] ?? String(event.payload.status ?? action?.status ?? "等待")}</b></span>
    <span><small>风险</small><b>{action?.risk ?? "未声明"}</b></span><span><small>耗时</small><b>{event.payload.duration_ms != null ? `${event.payload.duration_ms} ms` : "-"}</b></span>
    <span><small>结果保存</small><b>{artifactIds.length ? "证据存储" : "无证据产物"}</b></span>
    <span><small>错误码</small><b>{errorCode ?? "-"}</b></span>
    {action?.rationale ? <p><small>执行理由</small>{action.rationale}</p> : null}{action?.expected_outcome ? <p><small>预期结果</small>{action.expected_outcome}</p> : null}
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
    <header><FileSearch size={20} /><div><span>任务证据</span><h2>证据与结果</h2></div></header>
    <nav><button className={tab === "artifacts" ? "active" : ""} onClick={() => setTab("artifacts")}>证据产物 {snapshot.artifacts.length}</button><button className={tab === "result" ? "active" : ""} onClick={() => setTab("result")}>最终结果</button></nav>
    <div className="react-panel-scroll">
      {tab === "artifacts" ? snapshot.artifacts.slice().reverse().map((artifact) => { const origin = artifactOrigin(snapshot, artifact.id); return <article className="evidence-card" key={artifact.id}>
        <div className="evidence-card-main"><div className="evidence-card-heading"><span className="runtime-state">{artifactKindLabel(artifact.kind)}</span><span className="artifact-turn">{origin.turn ? `第 ${origin.turn} 轮` : "任务级"}</span></div><b>{toolLabel(artifact.tool ?? artifact.kind)}</b><code title={artifact.id}>{artifact.id}</code><small>{origin.action ? `由 ${toolLabel(origin.action)} 生成` : artifact.created_at ? `生成于 ${formatTime(artifact.created_at)}` : "任务证据产物"}</small></div>
        <div className="artifact-actions"><a href={runtimeApi.artifactUrl(snapshot.task.id, artifact.id)} target="_blank" rel="noreferrer"><ExternalLink size={14} />预览</a><a href={runtimeApi.artifactDownloadUrl(snapshot.task.id, artifact.id)} target="_blank" rel="noreferrer"><Download size={14} />下载</a></div>
      </article>; }) : null}
      {tab === "artifacts" && !snapshot.artifacts.length ? <Empty title="暂无证据" detail="工具输出持久化后会显示在这里。" /> : null}
      {tab === "result" ? <FinalResult snapshot={snapshot} confirmed={confirmed} event={finished} /> : null}
    </div>
  </aside>;
}

function FinalResult({ snapshot, confirmed, event }: { snapshot: RuntimeSnapshot; confirmed: boolean; event?: RuntimeEvent }) {
  if (!confirmed) return <Empty title={snapshot.session.status === "running" ? "尚未确认最终结果" : statusLabels[snapshot.session.status] ?? snapshot.session.status} detail={snapshot.session.stop_reason || "只有 FINISH_ACCEPTED 与 AGENT_FINISHED 同时存在时才展示确认结果。"} />;
  const summary = formatResultSummary(String(event?.payload.summary ?? "任务完成"));
  return <article className="final-result" data-testid="final-result"><header><ShieldCheck size={30} /><div><span>已确认最终结果</span><h3>{summary.title}</h3></div></header>
    {summary.intro ? <p className="final-result-intro">{summary.intro}</p> : null}
    {summary.sections.map((section) => <section className="final-result-section" key={`${section.title}-${section.lines[0]}`}><b>{section.title}</b>{section.lines.length > 1 ? <ol>{section.lines.map((line) => <li key={line}>{line}</li>)}</ol> : <p>{section.lines[0]}</p>}</section>)}
    {event?.payload.coverage?.length ? <section><b>覆盖范围</b><ul>{event.payload.coverage.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
    {event?.payload.limitations?.length ? <section><b>限制</b><ul>{event.payload.limitations.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
    {snapshot.flags.length ? <section className="final-flags"><b>确认结果</b>{snapshot.flags.map((flag) => <a key={flag.value} href={runtimeApi.artifactUrl(snapshot.task.id, flag.evidence_artifact_id)} target="_blank" rel="noreferrer"><code>{flag.value}</code><ExternalLink size={14} /></a>)}</section> : null}
    <section className="final-result-footer"><ArtifactLinks taskId={snapshot.task.id} ids={event?.payload.evidence_artifact_ids ?? []} /><small>结束原因：{stopReasonLabel(snapshot.session.stop_reason || "finish_accepted")}</small></section>
  </article>;
}

function ArtifactLinks({ taskId, ids }: { taskId: string; ids: string[] }) { const unique = [...new Set(ids)]; return unique.length ? <div className="artifact-links">{unique.map((id) => <a key={id} href={runtimeApi.artifactUrl(taskId, id)} target="_blank" rel="noreferrer">{id}<ExternalLink size={11} /></a>)}</div> : null; }
function State({ value }: { value: string }) { return <span className={`runtime-state ${value}`}>{statusLabels[value] ?? memoryKindLabels[value] ?? value}</span>; }
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
function executionLocationLabel(value: string) { return ({ "Remote MCP Service": "远程 MCP 服务", "Session Workspace": "任务工作区", "Authorized HTTP Target": "已授权 HTTP 目标", "Input Store": "输入存储", "TGA Process": "TGA 本地进程" } as Record<string, string>)[value] ?? value; }
function readableType(type: string) { return type.split("_").map((part) => part.charAt(0) + part.slice(1).toLowerCase()).join(" "); }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString("zh-CN", { hour12: false }); }
function boundedJson(value: Record<string, unknown>) { const encoded = JSON.stringify(value); return encoded.length > 600 ? `${encoded.slice(0, 600)}...` : encoded; }
function artifactOrigin(snapshot: RuntimeSnapshot, artifactId: string) {
  for (const event of [...snapshot.events].sort((a, b) => b.seq - a.seq)) {
    const ids = new Set([...(event.payload.artifact_ids ?? []), ...(event.payload.artifacts?.map((item) => item.artifact_id) ?? []), ...(event.payload.evidence_artifact_ids ?? []), event.payload.artifact_id].filter((item): item is string => typeof item === "string"));
    if (!ids.has(artifactId)) continue;
    const action = event.payload.action_id ? snapshot.actions.find((item) => item.id === event.payload.action_id) : snapshot.actions.find((item) => item.artifact_ids.includes(artifactId));
    const declaredTurn = typeof event.payload.turn === "number" ? event.payload.turn : undefined;
    const grouped = declaredTurn ?? groupTurns(snapshot.events).find((turn) => turn.events.some((item) => item.seq === event.seq))?.number;
    return { turn: grouped, action: String(event.payload.tool_name ?? action?.capability ?? "") || undefined };
  }
  const action = snapshot.actions.find((item) => item.artifact_ids.includes(artifactId));
  if (!action) return {};
  const event = snapshot.events.find((item) => item.payload.action_id === action.id && typeof item.payload.turn === "number");
  return { turn: event?.payload.turn, action: action.capability };
}
function artifactKindLabel(value: string) { return ({ tool_output: "工具输出", http_body: "响应正文", http_response: "HTTP 响应", report: "报告", evidence: "证据" } as Record<string, string>)[value] ?? value; }
function toolLabel(value: string) { return ({ "http.request": "HTTP 请求", "http.request.body": "HTTP 响应正文", "workspace.read": "读取工作区文件", "workspace.python": "执行 Python", "workspace.shell": "执行 Shell", "artifact.inspect": "检查证据产物" } as Record<string, string>)[value] ?? value.replace(/^tga_/, "").replace(/_/g, " "); }
function stopReasonLabel(value: string) { return ({ finish_accepted: "完成校验已通过", user_paused: "用户暂停", user_cancelled: "用户取消", model_request_failed: "模型请求失败" } as Record<string, string>)[value] ?? value; }
function localizeStrategyText(value: string) {
  const replacements: Array<[string, string]> = [
    ["Initial task strategy", "初始任务策略"],
    ["Candidate strategy from user hint", "来自用户提示的候选策略"],
    ["Untrusted candidate guidance:", "未经验证的候选指引："],
    ["Fetch and extract the scoped reference", "抓取并提取已授权范围内的参考内容"],
    ["Validate the supplied hint against the authorized target", "在已授权目标上验证用户提供的提示"],
    ["readable document segments with Artifact provenance", "已提取带证据来源的可读文档片段"],
    ["an Artifact-backed observation", "获得由证据产物支持的观察结果"],
    ["scope and target-version validation", "范围和目标版本校验"],
    ["explicit form request", "明确的表单请求"],
    ["evidence-producing request", "可生成证据的请求"],
    ["readable body extracted", "已提取可读正文"],
    ["body extraction failed", "正文提取失败"],
  ];
  return localizeRuntimeText(replacements.reduce((text, [source, target]) => text.split(source).join(target), value));
}
const memoryKindLabels: Record<string, string> = {
  fact: "事实", evidence: "证据", failure_boundary: "失败边界", hint: "用户提示", constraint: "约束", decision: "决策",
};
function localizeRuntimeText(value: string) {
  const replacements: Array<[RegExp, string]> = [
    [/Consecutive failures require a new diagnosis before retry:\s*/gi, "连续失败，重试前必须重新诊断："],
    [/semantic repeat requires a reason tied to new evidence, changed parameters, or explicit verification/gi, "语义重复：必须说明与新证据、参数变化或明确验证目的相关的重试理由"],
    [/Change evidence, parameters, or validation purpose before retrying\.?/gi, "重试前请更换证据、参数或验证目的。"],
    [/Supply a retry reason tied to new evidence, changed parameters, or explicit verification\.?/gi, "请提供与新证据、参数变化或明确验证目的相关的重试理由。"],
    [/The success marker was not observed; validate encoding, parameters, and prerequisites\.?/gi, "未观察到成功标记；请检查编码、参数和前置条件。"],
    [/Diagnose HTTP session continuity before increasing side effects\.?/gi, "提高操作影响前，请先诊断 HTTP 会话连续性。"],
    [/Use bounded artifact retrieval and retain only source references and durable conclusions\.?/gi, "请限制证据产物读取范围，仅保留来源引用和可复用结论。"],
    [/Record expected side effects and compare a lower-impact evidence path first\.?/gi, "请记录预期副作用，并优先比较影响更低的取证路径。"],
    [/workspace shell exited\s+(-?\d+)/gi, "工作区 Shell 退出码为 $1"],
    [/workspace shell timed out/gi, "工作区 Shell 执行超时"],
    [/wrote\s+([^\n|]+?)(?=\s*(?:\||$))/gi, "已写入 $1"],
    [/read\s+([^\n|]+?)(?=\s*(?:\||$))/gi, "已读取 $1"],
  ];
  let localized = replacements.reduce((text, [pattern, target]) => text.replace(pattern, target), value);
  localized = localized.replace(
    /Enter Shell Code:\s*([\s\S]*?)\s*Execute Code Execution Result:\s*Is\s+([\s\S]*?)\s+execute success!?/gi,
    (_match, command: string, result: string) => `输入 Shell 命令：${command.trim()}\n执行结果：${result.trim()} 执行成功。`,
  );
  return localized;
}
function formatResultSummary(value: string) {
  const clean = value.replace(/\\n/g, "\n").replace(/\*\*/g, "").replace(/^#+\s*/gm, "").trim();
  const marker = /(?:Challenge Analysis|Exploitation|Flag|Result|Summary|分析|利用过程|最终结果|结论)\s*[:：]/i;
  const firstMarker = clean.search(marker);
  const headingText = firstMarker >= 0 ? clean.slice(0, firstMarker).trim() : clean.split(/\n+/)[0]?.trim() || "任务完成";
  const sectionText = firstMarker >= 0 ? clean.slice(firstMarker) : clean.split(/\n+/).slice(1).join("\n");
  const titleSource = headingText.replace(/\s+at\s+https?:\/\/\S+\/?[.]?$/i, "").trim();
  const title = titleSource.length > 72 ? "任务已完成并通过校验" : titleSource.replace(/^Solved\s+/i, "已完成：");
  const sections: Array<{ title: string; lines: string[] }> = [];
  let intro = "";
  const chunks = sectionText.split(/(?=(?:Challenge Analysis|Exploitation|Flag|Result|Summary|分析|利用过程|最终结果|结论)\s*[:：])/i).filter(Boolean);
  for (const chunk of chunks) {
    const match = chunk.match(/^(Challenge Analysis|Exploitation|Flag|Result|Summary|分析|利用过程|最终结果|结论)\s*[:：]\s*([\s\S]*)$/i);
    if (!match) { intro = [intro, chunk].filter(Boolean).join(" "); continue; }
    const heading = ({ "challenge analysis": "任务分析", exploitation: "执行过程", flag: "最终答案", result: "最终结果", summary: "结果摘要" } as Record<string, string>)[match[1].toLowerCase()] ?? match[1];
    const body = match[2].replace(/`/g, "").trim();
    const lines = body.split(/\s+(?=\d+[.、]\s*)/).map((line) => line.replace(/^\d+[.、]\s*/, "").trim()).filter(Boolean);
    if (lines.length) sections.push({ title: heading, lines });
  }
  if (!sections.length && sectionText) sections.push({ title: "结果摘要", lines: sectionText.split(/\n+/).map((line) => line.replace(/`/g, "")).filter(Boolean) });
  return { title, intro: intro.replace(/`/g, ""), sections };
}
export function RuntimeLoading({ error, onRetry }: { error: string | null; onRetry: () => void }) { return <div className="runtime-loading"><TerminalSquare size={28} /><b>{error ? "运行时加载失败" : "正在读取 Session"}</b><p>{error ?? "正在加载初始 Snapshot 并连接增量事件流。"}</p>{error ? <button onClick={onRetry}>重试</button> : null}</div>; }
export function redact(value: string) { return value.replace(/(authorization|cookie|token|secret|api[_-]?key|password)\s*[:=]\s*[^\s,;]+/gi, "$1=[REDACTED]"); }
