import { ArrowLeft, BookOpen, Clock, Download, Flag } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { apiBase, requestJson } from "../../../api/client";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import type { AOEQuestion, TGA3RuntimeSnapshot, TGA3TaskEvent } from "../../../runtime/tga3-runtime";
import { formatTime } from "../tga3-view";
import { aoeBudgets, attemptGroups, attemptLabel, matchesQuestionFilter, QUESTION_FILTERS, sortQuestions, type QuestionFilter } from "../aoe-view";
import { useTGA3Runtime } from "../use-tga3-runtime";
import { EventCard, RealtimePrompt, TGA3Workspace, type TGA3WorkspaceTab } from "./TGA3Workspace";
import { AOEScoutStandbyBar } from "./AOEScoutStandbyBar";

const STATUS: Record<string, string> = { queued: "待解题", running: "解题中", solved: "已解出", abandoned: "已放弃", cancelled: "已取消" };
const PHASE: Record<string, string> = { collecting: "收集题目", executing: "基础侦察", extracting: "提取情报", merging: "按题合并", solo: "独立解题", joint: "共同攻坚", swarm: "共同攻坚（群殴）", awaiting_finish_confirm: "待确认结束", standby: "Scout 站岗候题", reporting: "生成逐题报告", completed: "已结束", failed: "运行失败", cancelled: "已取消" };
const REPORT: Record<string, string> = { pending: "等待解题结束", generating: "生成中", completed: "已生成", failed: "生成失败", disabled: "未启用报告", pre_solved: "任务前已解出，无需报告" };

export function AOEWorkspace({ snapshot, tab, selectedAgentId, onTab, onChanged }: {
  snapshot: TGA3RuntimeSnapshot; tab: TGA3WorkspaceTab; selectedAgentId: string | null;
  onTab: (tab: TGA3WorkspaceTab) => void; onChanged: () => void;
}) {
  const overview = snapshot.aoe!;
  const location = useLocation(), navigate = useNavigate();
  const requested = new URLSearchParams(location.search).get("question") ?? "";
  const filterValue = new URLSearchParams(location.search).get("question_status");
  const filter: QuestionFilter = QUESTION_FILTERS.find(([value]) => value === filterValue)?.[0] ?? "all";
  const questions = sortQuestions(overview.questions).filter(item => matchesQuestionFilter(item, filter));
  const budgets = aoeBudgets(overview);
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
  function chooseFilter(value: QuestionFilter) {
    const params = new URLSearchParams(location.search);
    if (value === "all") params.delete("question_status"); else params.set("question_status", value);
    if (question && !matchesQuestionFilter(question, value)) {
      params.delete("question"); params.set("tab", "blackboard"); onTab("blackboard");
    }
    navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
  }
  const waiting = <div className="tga3-empty"><h3>{detail.error ?? "正在读取本题黑板与记录"}</h3>{detail.error ? <button onClick={detail.refresh}>重试</button> : null}</div>;
  const panels: Parameters<typeof TGA3Workspace>[0]["panels"] = {
    events: <AOEEvents snapshot={snapshot} questionId={question?.question_id} selectedAgentId={selectedAgentId} />,
  };
  if (!question) {
    panels.blackboard = <>
      <AOEOverviewPanel snapshot={snapshot} questions={questions} onOpen={id => choose(id, "blackboard")} onChanged={onChanged} name={name} />
      <RealtimePrompt taskId={snapshot.task.id} accepting={["scouting", "running"].includes(snapshot.task.state)} onChanged={onChanged} />
    </>;
    panels.findings = <div className="tga3-empty"><h3>按题查看 Findings</h3><p>在上方选择题目，查看它的已验证事实与最终候选。</p></div>;
    panels.report = <div className="aoe-report-list">{questions.length ? questions.map(item => <article key={item.question_id}>
      <div><strong>{item.title}</strong><small>{item.pre_solved ? "开启任务前已解出" : STATUS[item.status] ?? item.status} · {REPORT[item.report_state]}</small></div>
      <button className="ref-secondary-button" onClick={() => choose(item.question_id, "report")}>查看本题报告</button>
    </article>) : <p>{overview.questions.length ? "当前筛选下暂无题目。" : "Scout 登记题目后，逐题报告会列在这里。"}</p>}</div>;
  } else if (!child) {
    for (const value of ["blackboard", "findings", "inputs", "report"] as const) panels[value] = waiting;
  } else if (question.pre_solved || question.report_state === "pre_solved") {
    panels.report = <div className="tga3-empty"><h3>开启任务前已解出</h3><p>该题在开启本次任务前已在平台解出，跳过求解与报告生成。</p></div>;
  } else if (question.report_state === "disabled") {
    panels.report = <div className="tga3-empty"><h3>本任务未启用产出报告</h3><p>Reporter 仅整理经验卡，执行记录和 Findings 仍可查看。</p></div>;
  } else if (question.report_state === "failed") {
    panels.report = <div className="tga3-empty"><h3>本题报告生成失败</h3><p>解题状态和已有证据已保留，可在 Reporter 事件中查看失败原因。</p></div>;
  } else if (question.report_state === "pending" && question.status === "abandoned") {
    panels.report = <div className="tga3-empty"><h3>正在准备未解出题目的报告</h3><p>报告会保留已验证的方法、结果和失败边界。</p></div>;
  }
  return <div className="aoe-workspace">
    <AOEScoutStandbyBar taskId={snapshot.task.id} taskState={snapshot.task.state} overview={overview} onChanged={onChanged} />
    <div className="aoe-scope-toolbar">
      <label>题目<select aria-label="题目筛选" value={question?.question_id ?? ""} onChange={event => choose(event.target.value)}>
        <option value="">全部题目 / 总览</option>
        {questions.map(item => <option key={item.question_id} value={item.question_id}>{item.title}{item.status === "running" ? " · 解题中" : ""}</option>)}
        {question && !questions.some(item => item.question_id === question.question_id) ? <option value={question.question_id}>{question.title} · 当前题目</option> : null}
      </select></label>
      <label>状态<select aria-label="解题状态筛选" value={filter} onChange={event => chooseFilter(event.target.value as QuestionFilter)}>
        {QUESTION_FILTERS.map(([value, label]) => <option key={value} value={value}>{label}（{overview.questions.filter(q => matchesQuestionFilter(q, value)).length}）</option>)}
      </select></label>
      <span>{overview.phase === "swarm" ? PHASE.swarm : overview.scout_mode === "collection_only" && !overview.collection_complete ? "收集题目与解题并行" : PHASE[overview.phase] ?? overview.phase} · 首次 {budgets.first / 60} 分钟 · 第二次 {budgets.second / 60} 分钟 · 共同攻坚 {budgets.joint / 60} 分钟{overview.scout_mode === "collection_only" ? " · Scout 仅收集题面与附件" : ""}</span>
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

const EXTEND_PRESETS = [5, 10, 15, 30] as const;

function AOEOverviewPanel({ snapshot, questions, onOpen, onChanged, name }: { snapshot: TGA3RuntimeSnapshot; questions: AOEQuestion[]; onOpen: (id: string) => void; onChanged: () => void; name: (id: string) => string }) {
  const overview = snapshot.aoe!;
  const [now, setNow] = useState(Date.now() / 1000);
  const [extendTarget, setExtendTarget] = useState<AOEQuestion | null>(null);
  const [extendMinutes, setExtendMinutes] = useState("5");
  const [extending, setExtending] = useState(false);
  const [extendError, setExtendError] = useState("");
  const [extendNotice, setExtendNotice] = useState("");
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000); return () => window.clearInterval(timer); }, []);
  if (!overview.questions.length) return <div className="tga3-empty"><BookOpen /><h3>Scout 正在收集题目</h3><p>题面与附件登记后即可分配；收集期间先解简单题，无难度说明时直接分配。</p></div>;
  const parentTerminal = ["completed", "failed", "cancelled", "stopped"].includes(snapshot.task.state);
  const solved = overview.questions.filter(item => item.status === "solved").length;
  const preSolved = overview.questions.filter(item => item.pre_solved).length;
  const abandoned = overview.questions.filter(item => item.status === "abandoned").length;
  const preparing = overview.questions.filter(item => item.scout_ready === false).length;

  const parsedMinutes = Number(extendMinutes);
  const validMinutes = Number.isInteger(parsedMinutes) && parsedMinutes >= 1 && parsedMinutes <= 240;
  const liveTarget = extendTarget ? overview.questions.find(q => q.question_id === extendTarget.question_id) ?? extendTarget : null;
  const targetActive = liveTarget ? liveTarget.attempts.filter(attempt => attempt.outcome === null) : [];
  const targetDeadline = targetActive[0]?.deadline ?? null;
  const targetSeconds = targetDeadline == null ? null : Math.max(0, Math.ceil(targetDeadline - now));

  function openExtend(item: AOEQuestion) {
    setExtendTarget(item);
    setExtendMinutes("5");
    setExtendError("");
  }

  async function confirmExtend() {
    if (!liveTarget || extending || !validMinutes) return;
    setExtending(true);
    setExtendError("");
    try {
      await tga3RuntimeApi.extendQuestion(snapshot.task.id, liveTarget.question_id, parsedMinutes);
      setExtendNotice(`已为「${liveTarget.title}」本轮延长 ${parsedMinutes} 分钟。`);
      setExtendTarget(null);
      onChanged();
    } catch (reason) {
      setExtendError(reason instanceof Error ? reason.message : "延长时间失败");
    } finally {
      setExtending(false);
    }
  }

  return <section className="aoe-overview" aria-label="题目总览">
    <header><h3>{questions.length === overview.questions.length ? overview.questions.length : `${questions.length} / ${overview.questions.length}`} 道题目</h3><span>{solved} 已解出{preSolved ? `（含开启任务前已解出 ${preSolved}）` : ""} · {abandoned} 已放弃{preparing ? ` · ${preparing} 待交接` : ""}</span></header>
    {extendNotice ? <div className="aoe-extend-notice" role="status"><span>{extendNotice}</span><button type="button" className="text-button" onClick={() => setExtendNotice("")}>关闭</button></div> : null}
    {!questions.length ? <p className="tga3-muted">当前筛选下暂无题目。</p> : null}
    <div className="aoe-question-roster">{questions.map(question => {
      const active = question.attempts.filter(attempt => attempt.outcome === null);
      const stages = attemptGroups(question);
      const deadline = active[0]?.deadline;
      const seconds = deadline == null ? null : Math.max(0, Math.ceil(deadline - now));
      const extendedMinutes = Math.round((active[0]?.manual_extension_seconds ?? 0) / 60);
      const canShowExtend = !parentTerminal && question.status === "running";
      const countdownStarted = seconds !== null && active.length > 0;
      return <article key={question.question_id} className="aoe-question-row" data-status={question.status}>
        <header><div><button className="text-button aoe-question-title" onClick={() => onOpen(question.question_id)}>{question.title}</button>
          <small>{question.difficulty === null ? "未标注难度" : `难度：${question.difficulty}`}</small></div><QuestionStatus question={question} /></header>
        <div className="aoe-question-ownership"><span>已尝试<strong>{question.attempted_by.map(name).join("、") || "尚无"}</strong></span>
          <span>正在解题<strong>{question.current_agents.map(name).join("、") || "暂无"}</strong></span>
          {seconds !== null ? <span>{attemptLabel(question, active[0])}剩余<strong>{Math.floor(seconds / 60)}分{seconds % 60}秒{extendedMinutes > 0 ? <em className="aoe-extended-tag">（已延长 +{extendedMinutes}分）</em> : null}</strong></span> : null}
        </div>
        {question.flag ? <div className="aoe-question-answer"><Flag size={14} /><code className="aoe-flag">{question.flag}</code></div> : null}
        <QuestionMaterials question={question} />
        <footer><button className="ref-secondary-button" onClick={() => onOpen(question.question_id)}><BookOpen size={14} />进入黑板详情 <small>{question.blackboard_seq}</small></button>
          {canShowExtend ? (
            <button
              type="button"
              className="ref-secondary-button aoe-extend-button"
              disabled={!countdownStarted}
              title={countdownStarted ? "延长本题当前轮次时间" : "等待 Worker 执行首个动作开始倒计时后可延长"}
              aria-label={`延长本轮时间 ${question.title}`}
              onClick={() => openExtend(question)}
            >
              <Clock size={14} />{countdownStarted ? "延长本轮时间" : "延长本轮（未开始计时）"}
            </button>
          ) : null}
          <details className="aoe-attempt-history"><summary>尝试记录（{stages.length}）</summary>
            {stages.length ? <ol>{stages.map(stage => <li key={stage.id}>
              <strong>{stage.agents.map(name).join("＋")}</strong> · {stage.label} · {stage.outcome}
              <small>{stage.startedAt == null ? "准备执行，尚未开始计时" : formatTime(new Date(stage.startedAt * 1000).toISOString())}{stage.finishedAt != null ? ` — ${formatTime(new Date(stage.finishedAt * 1000).toISOString())}` : ""}</small>
            </li>)}</ol> : <p>尚未分配 Worker。</p>}
          </details>
        </footer>
      </article>;
    })}</div>

    {liveTarget ? (
      <div className="dialog-backdrop" role="presentation">
        <section className="confirm-dialog aoe-extend-dialog" role="dialog" aria-modal="true" aria-labelledby="extend-dialog-title">
          <h2 id="extend-dialog-title">确认延长本轮解题时间</h2>
          <p className="aoe-extend-warning">防误触提醒：请核对下方题目名称与当前轮次，确认后将立即同步顺延所有正在解本题的 Worker 截止时间，不会中断当前执行。</p>
          <div className="aoe-extend-meta">
            <div><small>目标题目</small><strong>{liveTarget.title}</strong></div>
            <div><small>当前阶段 / Worker</small><strong>{targetActive[0] ? attemptLabel(liveTarget, targetActive[0]) : "解题中"} · {liveTarget.current_agents.map(name).join("、") || "暂无"}</strong></div>
            {targetSeconds !== null ? (
              <div>
                <small>剩余时间预览</small>
                <strong>
                  {Math.floor(targetSeconds / 60)}分{targetSeconds % 60}秒
                  {validMinutes ? ` → ${Math.floor((targetSeconds + parsedMinutes * 60) / 60)}分${(targetSeconds + parsedMinutes * 60) % 60}秒` : ""}
                </strong>
              </div>
            ) : null}
          </div>
          <div className="aoe-extend-input-block">
            <label>
              <span>延长时间（分钟，1–240）</span>
              <input
                type="number"
                aria-label="延长分钟数"
                min={1}
                max={240}
                step={1}
                value={extendMinutes}
                onChange={(event) => setExtendMinutes(event.target.value)}
                autoFocus
              />
            </label>
            <div className="aoe-extend-presets" role="group" aria-label="快捷延长时间">
              {EXTEND_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  className={`ref-secondary-button${parsedMinutes === preset ? " is-selected" : ""}`}
                  onClick={() => setExtendMinutes(String(preset))}
                >
                  +{preset} 分钟
                </button>
              ))}
            </div>
          </div>
          {extendError ? <p className="inline-error" role="alert">{extendError}</p> : null}
          <div>
            <button type="button" className="secondary-button" disabled={extending} onClick={() => setExtendTarget(null)}>取消</button>
            <button type="button" className="ref-primary-button" disabled={extending || !validMinutes} onClick={() => void confirmExtend()}>
              {extending ? "正在延长…" : validMinutes ? `确认延长 +${parsedMinutes} 分钟` : "请输入有效分钟数"}
            </button>
          </div>
        </section>
      </div>
    ) : null}
  </section>;
}

function QuestionStatus({ question }: { question: AOEQuestion }) {
  if (question.pre_solved) {
    return <span className="aoe-question-status status-solved status-pre-solved">开启任务前已解出</span>;
  }
  if (question.scout_ready === false && ["preparing", "queued"].includes(question.status)) {
    const phases: Record<string, string> = { queued: "待侦察", executing: "第一轮侦察", extracting: "第二轮提取", merging: "第三轮合并" };
    return <span className="aoe-question-status status-preparing">{phases[question.scout_phase ?? "queued"] ?? "准备中"}</span>;
  }
  const collaborative = question.attempts.find(a => a.outcome === null && (a.phase === "swarm" || a.phase === "joint"));
  const support = question.status === "running" && question.attempts.some(a => a.phase === "reinforcement" && a.outcome === null)
    ? "增援模式" : question.status === "running" && question.rescue?.state === "seeking" ? "求援中" : null;
  return <span className={`aoe-question-status status-${question.status}`}>{collaborative ? attemptLabel(question, collaborative) : support ?? (question.finish_reason === "joint_failed" ? "共同攻坚失败，已放弃" : STATUS[question.status] ?? question.status)}</span>;
}

function QuestionMaterials({ question }: { question: AOEQuestion }) {
  return <details className="aoe-question-materials"><summary>题面与附件（{question.attachments.length}）</summary>
    <p>{question.description}</p>
    {question.connection_info ? <div><strong>连接信息</strong><p>{question.connection_info}</p></div> : null}
    {question.submission_instructions ? <div><strong>提交要求</strong><p>{question.submission_instructions}</p></div> : null}
    {question.collection_notes ? <div><strong>收集备注</strong><p>{question.collection_notes}</p></div> : null}
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
