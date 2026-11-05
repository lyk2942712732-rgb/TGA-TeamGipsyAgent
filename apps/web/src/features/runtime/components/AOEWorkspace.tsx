import { ArrowLeft, BookOpen, Download, Flag } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { apiBase, requestJson } from "../../../api/client";
import type { AOEQuestion, TGA3RuntimeSnapshot, TGA3TaskEvent } from "../../../runtime/tga3-runtime";
import { formatTime } from "../tga3-view";
import { useTGA3Runtime } from "../use-tga3-runtime";
import { EventCard, RealtimePrompt, TGA3Workspace, type TGA3WorkspaceTab } from "./TGA3Workspace";

const STATUS: Record<string, string> = { queued: "待解题", running: "解题中", solved: "已解出", abandoned: "已放弃", cancelled: "已取消" };
const PHASE: Record<string, string> = { collecting: "收集题目", executing: "基础侦察", extracting: "提取情报", merging: "按题合并", solo: "独立解题", joint: "共同攻坚", reporting: "生成逐题报告", completed: "已结束", failed: "运行失败", cancelled: "已取消" };
const REPORT: Record<string, string> = { pending: "等待解题结束", generating: "生成中", completed: "已生成", failed: "生成失败" };

export function AOEWorkspace({ snapshot, tab, selectedAgentId, onTab, onChanged }: {
  snapshot: TGA3RuntimeSnapshot; tab: TGA3WorkspaceTab; selectedAgentId: string | null;
  onTab: (tab: TGA3WorkspaceTab) => void; onChanged: () => void;
}) {
  const overview = snapshot.aoe!;
  const location = useLocation(), navigate = useNavigate();
  const requested = new URLSearchParams(location.search).get("question") ?? "";
  const question = overview.questions.find(item => item.question_id === requested);
  const detail = useTGA3Runtime(question?.question_id ?? null);
  const child = question && detail.snapshot?.task.id === question.question_id ? detail.snapshot : null;
  const name = (id: string) => snapshot.agents.find(agent => agent.agent_id === id)?.display_name ?? id;
  function choose(id: string, nextTab = tab) {
    const params = new URLSearchParams(location.search);
    if (id) params.set("question", id); else params.delete("question");
    params.set("tab", nextTab); onTab(nextTab);
    navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
  }
  const waiting = <div className="tga3-empty"><h3>{detail.error ?? "正在读取本题黑板与记录"}</h3>{detail.error ? <button onClick={detail.refresh}>重试</button> : null}</div>;
  const panels: Parameters<typeof TGA3Workspace>[0]["panels"] = {
    events: <AOEEvents snapshot={snapshot} questionId={question?.question_id} selectedAgentId={selectedAgentId} />,
  };
  if (!question) {
    panels.blackboard = <>
      <AOEOverviewPanel snapshot={snapshot} onOpen={id => choose(id, "blackboard")} name={name} />
      <RealtimePrompt taskId={snapshot.task.id} accepting={["scouting", "running"].includes(snapshot.task.state)} onChanged={onChanged} />
    </>;
    panels.findings = <div className="tga3-empty"><h3>按题查看 Findings</h3><p>在上方选择题目，查看它的已验证事实与最终候选。</p></div>;
    panels.report = <div className="aoe-report-list">{overview.questions.length ? overview.questions.map(item => <article key={item.question_id}>
      <div><strong>{item.title}</strong><small>{STATUS[item.status] ?? item.status} · {REPORT[item.report_state]}</small></div>
      <button className="ref-secondary-button" onClick={() => choose(item.question_id, "report")}>查看本题报告</button>
    </article>) : <p>Scout 登记题目后，逐题报告会列在这里。</p>}</div>;
  } else if (!child) {
    for (const value of ["blackboard", "findings", "inputs", "report"] as const) panels[value] = waiting;
  } else if (question.report_state === "failed") {
    panels.report = <div className="tga3-empty"><h3>本题报告生成失败</h3><p>解题状态和已有证据已保留，可在 Reporter 事件中查看失败原因。</p></div>;
  } else if (question.report_state === "pending" && question.status === "abandoned") {
    panels.report = <div className="tga3-empty"><h3>正在准备未解出题目的报告</h3><p>报告会保留已验证的方法、结果和失败边界。</p></div>;
  }
  return <div className="aoe-workspace">
    <div className="aoe-scope-toolbar">
      <label>题目<select aria-label="题目筛选" value={question?.question_id ?? ""} onChange={event => choose(event.target.value)}>
        <option value="">全部题目 / 总览</option>
        {overview.questions.map(item => <option key={item.question_id} value={item.question_id}>{item.title}</option>)}
      </select></label>
      <span>{overview.scout_mode === "collection_only" && !overview.collection_complete ? "收集题目与解题并行" : PHASE[overview.phase] ?? overview.phase} · 每次尝试 {overview.attempt_seconds / 60} 分钟{overview.scout_mode === "collection_only" ? " · Scout 仅收集题面与附件" : ""}</span>
    </div>
    {requested && !question ? <p role="alert">该题目不属于当前任务，或尚未完成登记。</p> : null}
    {question ? <section className="aoe-question-context">
      <button className="text-button" onClick={() => choose("", "blackboard")}><ArrowLeft size={14} />返回总览</button>
      <div><h3>{question.title}</h3><QuestionStatus question={question} /></div>
      <p>已尝试：{question.attempted_by.map(name).join("、") || "尚无"}　正在解题：{question.current_agents.map(name).join("、") || "暂无"}</p>
      {question.flag ? <code className="aoe-flag">{question.flag}</code> : null}
      <QuestionMaterials question={question} />
    </section> : null}
    <TGA3Workspace snapshot={child ?? snapshot} tab={tab} selectedAgentId={selectedAgentId} onTab={onTab}
      onChanged={() => { onChanged(); detail.refresh(); }} panels={panels}
      labels={question ? {} : { blackboard: "总览" }}
      counts={!question ? { blackboard: overview.questions.length, report: overview.questions.filter(item => item.report_state === "completed").length } : {}} />
  </div>;
}

function AOEOverviewPanel({ snapshot, onOpen, name }: { snapshot: TGA3RuntimeSnapshot; onOpen: (id: string) => void; name: (id: string) => string }) {
  const overview = snapshot.aoe!;
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000); return () => window.clearInterval(timer); }, []);
  if (!overview.questions.length) return <div className="tga3-empty"><BookOpen /><h3>Scout 正在收集题目</h3><p>题面与附件登记后即可分配；收集期间先解简单题，无难度说明时直接分配。</p></div>;
  const solved = overview.questions.filter(item => item.status === "solved").length;
  const abandoned = overview.questions.filter(item => item.status === "abandoned").length;
  const preparing = overview.questions.filter(item => item.scout_ready === false).length;
  return <section className="aoe-overview" aria-label="题目总览">
    <header><h3>{overview.questions.length} 道题目</h3><span>{solved} 已解出 · {abandoned} 已放弃{preparing ? ` · ${preparing} 待交接` : ""}</span></header>
    <div className="aoe-question-roster">{[...overview.questions].sort((a, b) => {
      const solvedA = a.status === "solved", solvedB = b.status === "solved";
      if (solvedA !== solvedB) return solvedA ? -1 : 1;
      const stamp = (q: AOEQuestion) => q.status === "solved"
        ? q.solved_at ?? Math.max(0, ...q.attempts.filter(t => t.outcome === "solved").map(t => t.finished_at ?? 0))
        : q.registered_at ?? 0;
      return stamp(b) - stamp(a);
    }).map(question => {
      const active = question.attempts.filter(attempt => attempt.outcome === null);
      const deadline = active[0]?.deadline;
      const seconds = deadline == null ? null : Math.max(0, Math.ceil(deadline - now));
      return <article key={question.question_id} className="aoe-question-row" data-status={question.status}>
        <header><div><button className="text-button aoe-question-title" onClick={() => onOpen(question.question_id)}>{question.title}</button>
          <small>{question.difficulty === null ? "未标注难度" : `难度：${question.difficulty}`}</small></div><QuestionStatus question={question} /></header>
        <div className="aoe-question-ownership"><span>已尝试<strong>{question.attempted_by.map(name).join("、") || "尚无"}</strong></span>
          <span>正在解题<strong>{question.current_agents.map(name).join("、") || "暂无"}</strong></span>
          {seconds !== null ? <span>{active[0]?.phase === "joint" ? "共同攻坚剩余" : "本次尝试剩余"}<strong>{Math.floor(seconds / 60)}分{seconds % 60}秒</strong></span> : null}
        </div>
        {question.flag ? <div className="aoe-question-answer"><Flag size={14} /><code className="aoe-flag">{question.flag}</code></div> : null}
        <QuestionMaterials question={question} />
        <footer><button className="ref-secondary-button" onClick={() => onOpen(question.question_id)}><BookOpen size={14} />进入黑板详情 <small>{question.blackboard_seq}</small></button>
          <details className="aoe-attempt-history"><summary>尝试记录（{question.attempts.length}）</summary>
            {question.attempts.length ? <ol>{question.attempts.map(attempt => <li key={attempt.id}>
              <strong>{name(attempt.agent_id)}</strong> · {attempt.phase === "joint" ? "共同攻坚" : "独立尝试"} · {attempt.outcome === null ? "进行中" : ({ solved: "已解出", timeout: "预算已到", interrupted: "已中断", error: "执行失败" })[attempt.outcome]}
              <small>{attempt.started_at == null ? "准备执行，尚未开始计时" : formatTime(new Date(attempt.started_at * 1000).toISOString())}{attempt.finished_at ? ` — ${formatTime(new Date(attempt.finished_at * 1000).toISOString())}` : ""}</small>
            </li>)}</ol> : <p>尚未分配 Worker。</p>}
          </details>
        </footer>
      </article>;
    })}</div>
  </section>;
}

function QuestionStatus({ question }: { question: AOEQuestion }) {
  if (question.scout_ready === false && ["preparing", "queued"].includes(question.status)) {
    const phases: Record<string, string> = { queued: "待侦察", executing: "第一轮侦察", extracting: "第二轮提取", merging: "第三轮合并" };
    return <span className="aoe-question-status status-preparing">{phases[question.scout_phase ?? "queued"] ?? "准备中"}</span>;
  }
  return <span className={`aoe-question-status status-${question.status}`}>{question.current_agents.length && question.attempts.some(a => a.phase === "joint" && a.outcome === null) ? "共同攻坚" : STATUS[question.status] ?? question.status}</span>;
}

function QuestionMaterials({ question }: { question: AOEQuestion }) {
  return <details className="aoe-question-materials"><summary>题面与附件（{question.attachments.length}）</summary>
    <p>{question.description}</p>
    {question.connection_info ? <div><strong>连接信息</strong><p>{question.connection_info}</p></div> : null}
    {question.submission_instructions ? <div><strong>提交要求</strong><p>{question.submission_instructions}</p></div> : null}
    {question.attachments.length ? <ul>{question.attachments.map(file => <li key={file.id}><a href={`${apiBase}/api/v3/tasks/${encodeURIComponent(question.parent_task_id)}/questions/${encodeURIComponent(question.question_id)}/files/${encodeURIComponent(file.id)}`}><Download size={13} />{file.name}</a><small>{file.size_bytes.toLocaleString()} 字节</small></li>)}</ul> : null}
  </details>;
}

function AOEEvents({ snapshot, questionId, selectedAgentId }: { snapshot: TGA3RuntimeSnapshot; questionId?: string; selectedAgentId: string | null }) {
  const [scope, setScope] = useState<"all" | "selected">("all"), [limit, setLimit] = useState(500);
  const [events, setEvents] = useState<TGA3TaskEvent[]>([]), [more, setMore] = useState(false), [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ limit: String(limit) });
    if (questionId) params.set("question_id", questionId);
    if (scope === "selected" && selectedAgentId) params.set("agent_id", selectedAgentId);
    void requestJson<{ events: TGA3TaskEvent[]; has_more: boolean }>(`/api/v3/tasks/${encodeURIComponent(snapshot.task.id)}/aoe/events?${params}`, { signal: controller.signal })
      .then(value => { setEvents(value.events); setMore(value.has_more); setError(""); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "无法读取事件"); });
    return () => controller.abort();
  }, [snapshot, questionId, selectedAgentId, scope, limit, nonce]);
  return <div className="tga3-events-panel"><div className="tga3-panel-toolbar"><div className="tga3-segment">
    <button aria-pressed={scope === "all"} onClick={() => setScope("all")}>全部 Agent</button>
    <button aria-pressed={scope === "selected"} disabled={!selectedAgentId} onClick={() => setScope("selected")}>当前 Agent</button>
  </div><small>{questionId ? "当前题目" : "全部题目与采集调度"} · 最近 {events.length} 条</small></div>
    {error ? <p role="alert">{error}<button onClick={() => setNonce(value => value + 1)}>重试</button></p> : null}
    {events.length ? <div className="tga3-events-list">{[...events].reverse().map(event => <div key={event.id}>
      {!questionId && (event.question_id || event.payload.question_id) ? <small className="aoe-event-question">{snapshot.aoe?.questions.find(q => q.question_id === (event.question_id ?? event.payload.question_id))?.title ?? "题目"}</small> : null}
      <EventCard message={event} />
    </div>)}</div> : <div className="tga3-empty"><h3>当前筛选下暂无执行事件</h3></div>}
    {more && limit < 10000 ? <button className="ref-secondary-button" onClick={() => setLimit(value => value + 500)}>加载更多事件</button> : null}
  </div>;
}
