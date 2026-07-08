import { FormEvent, useState } from "react";
import { createTask, fetchTaskSnapshot, reportUrl, TaskSnapshot, TGATask } from "./api";

const modeLabels: Record<TGATask["mode"], string> = {
  web_audit: "Web 漏洞审查",
  ctf: "CTF 解题",
  code_audit: "代码审计",
  binary_ctf: "二进制 CTF",
};

const intensityLabels: Record<TGATask["intensity"], string> = {
  passive: "被动",
  normal: "标准",
  active: "主动",
};

const intentLabels: Record<string, string> = {
  recon: "侦察探测",
  verify: "漏洞验证",
  exploit_ctf: "CTF 利用",
  code_scan: "代码扫描",
  report: "生成报告",
};

const eventLabels: Record<string, string> = {
  DECISION_TRACE: "决策记录",
  SAFETY_DECISION: "安全检查",
  INTENT_RESULT: "执行结果",
  ADAPTATION_DECISION: "策略调整",
  GATE_REJECTED: "证据门禁拒绝",
};

const knownText: Record<string, string> = {
  Ready: "就绪",
  "Run recon intent": "执行侦察探测",
  "Run verify intent": "执行漏洞验证",
  "Run exploit_ctf intent": "执行 CTF 利用",
  "Run code_scan intent": "执行代码扫描",
  "Run report intent": "生成报告",
  "Proceed to next planned intent": "继续执行下一步计划",
  "Use reconnaissance output to guide verification": "使用侦察结果指导后续验证",
  within_task_policy: "符合任务安全策略",
  ok: "成功",
  blocked: "已阻断",
  failed: "失败",
  TOOL_RUNNER_UNAVAILABLE: "MCP 工具运行器未配置",
  "Intent risk is compatible with task intensity and scope policy.": "该步骤风险等级与任务强度、授权范围兼容。",
  "No blocking condition was produced by the worker result.": "执行结果没有产生阻断条件。",
  "Map the reachable surface before making higher-risk decisions.": "先确认目标暴露面，再决定是否执行更高风险动作。",
  "Attempt flag recovery only after reconnaissance and require provenance before accepting a flag.":
    "在侦察之后尝试获取 flag，且只有真实输出中出现的 flag 才会被接受。",
  "Summarize confirmed evidence, rejected leads, artifacts, and limitations for review.":
    "汇总已确认的证据、被拒绝的线索、产物和限制条件，便于复核。",
  "Confirm candidate vulnerabilities with scoped evidence instead of reporting scanner guesses.":
    "只用范围内证据确认候选漏洞，不直接采信扫描器猜测。",
  "Use static tools for broad coverage, then rely on evidence gates before confirmation.":
    "先用静态工具扩大覆盖面，再通过证据门禁确认结果。",
};

function newTaskId() {
  return `task_${Math.random().toString(16).slice(2, 12)}`;
}

export default function App() {
  const [status, setStatus] = useState("就绪");
  const [reportTaskId, setReportTaskId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<TaskSnapshot | null>(null);
  const [task, setTask] = useState<TGATask>({
    id: newTaskId(),
    name: "本地靶机演示",
    mode: "ctf",
    target: "http://127.0.0.1:8080",
    scope: ["127.0.0.1:8080"],
    intensity: "normal",
    allow_active_scan: true,
    goal: "在授权范围内分析目标，尝试获取 flag，并生成可复核报告。",
    flag_format: "flag\\{[^}]+\\}",
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setStatus("任务执行中...");
    try {
      const result = await createTask(task);
      setReportTaskId(result.task_id);
      const taskSnapshot = await fetchTaskSnapshot(result.task_id);
      setSnapshot(taskSnapshot.snapshot);
      setStatus(`已完成：${result.task_id}`);
    } catch (error) {
      setStatus(error instanceof Error ? `执行失败：${error.message}` : "执行失败：未知错误");
    }
  }

  return (
    <main className="shell">
      <section className="hero">
        <div>
          <span className="eyebrow">Team Gipsy Agent</span>
          <h1>TGA 安全智能体控制台</h1>
          <p>面向授权靶机、CTF 和代码审计的任务编排、工具调用与证据留存界面。</p>
        </div>
        <output className="status-chip">{status}</output>
      </section>

      <section className="panel task-panel">
        <div className="panel-title">
          <div>
            <h2>新建任务</h2>
            <p>填写目标、授权范围和目标说明后，TGA 会生成计划并执行。</p>
          </div>
        </div>
        <form onSubmit={submit}>
          <div className="field-grid">
            <label>
              任务名称
              <input value={task.name} onChange={(e) => setTask({ ...task, name: e.target.value })} />
            </label>
            <label>
              任务模式
              <select value={task.mode} onChange={(e) => setTask({ ...task, mode: e.target.value as TGATask["mode"] })}>
                {Object.entries(modeLabels).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label>
            靶机地址
            <input value={task.target} onChange={(e) => setTask({ ...task, target: e.target.value })} />
          </label>
          <label>
            授权范围
            <input
              value={task.scope.join(",")}
              onChange={(e) => setTask({ ...task, scope: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
            />
          </label>

          <div className="field-grid">
            <label>
              扫描强度
              <select
                value={task.intensity}
                onChange={(e) => setTask({ ...task, intensity: e.target.value as TGATask["intensity"] })}
              >
                {Object.entries(intensityLabels).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Flag 格式
              <input
                value={task.flag_format ?? ""}
                onChange={(e) => setTask({ ...task, flag_format: e.target.value || null })}
                placeholder="flag\\{[^}]+\\}"
              />
            </label>
          </div>

          <label className="check-row">
            <input
              type="checkbox"
              checked={task.allow_active_scan}
              onChange={(e) => setTask({ ...task, allow_active_scan: e.target.checked })}
            />
            <span>允许在授权范围内执行主动探测</span>
          </label>

          <label>
            任务目标
            <textarea value={task.goal} onChange={(e) => setTask({ ...task, goal: e.target.value })} />
          </label>
          <button type="submit">启动任务</button>
        </form>
      </section>

      <TaskDashboard snapshot={snapshot} reportTaskId={reportTaskId} />
    </main>
  );
}

function TaskDashboard({ snapshot, reportTaskId }: { snapshot: TaskSnapshot | null; reportTaskId: string | null }) {
  const events = snapshot?.events ?? [];
  const plan = events.find((event) => event.type === "PLAN_CREATED")?.payload?.plan as
    | { steps?: Array<Record<string, unknown>> }
    | undefined;
  const traceEvents = events.filter((event) =>
    ["DECISION_TRACE", "SAFETY_DECISION", "INTENT_RESULT", "ADAPTATION_DECISION", "GATE_REJECTED"].includes(event.type),
  );

  return (
    <section className="panel result-panel">
      <div className="panel-title with-action">
        <div>
          <h2>运行证据</h2>
          <p>{reportTaskId ? `任务编号：${reportTaskId}` : "任务完成后，这里会展示计划、证据和决策轨迹。"}</p>
        </div>
        {reportTaskId ? (
          <a className="button-link" href={reportUrl(reportTaskId)} target="_blank" rel="noreferrer">
            打开报告
          </a>
        ) : null}
      </div>

      <div className="metrics">
        <Metric label="执行步骤" value={snapshot?.intents?.length ?? 0} />
        <Metric label="证据产物" value={snapshot?.artifacts?.length ?? 0} />
        <Metric label="漏洞发现" value={snapshot?.findings?.length ?? 0} />
        <Metric label="Flag" value={snapshot?.flags?.length ?? 0} />
      </div>

      <div className="columns">
        <section className="trace-section">
          <h3>自主计划</h3>
          <ol className="timeline">
            {(plan?.steps ?? []).map((step) => (
              <li key={String(step.intent_id)}>
                <strong>{intentLabel(String(step.kind))}</strong>
                <span>{translate(String(step.rationale ?? ""))}</span>
                <small>工具：{toolsLabel(step.required_tools)}</small>
              </li>
            ))}
            {!plan?.steps?.length ? <li className="empty-state">暂无计划记录</li> : null}
          </ol>
        </section>

        <section className="trace-section">
          <h3>决策轨迹</h3>
          <ol className="timeline compact">
            {traceEvents.slice(-10).map((event, index) => (
              <li key={`${event.type}-${event.id ?? index}`}>
                <strong>{eventLabels[event.type] ?? event.type}</strong>
                <span>{translate(eventSummary(event.payload))}</span>
              </li>
            ))}
            {!traceEvents.length ? <li className="empty-state">暂无决策记录</li> : null}
          </ol>
        </section>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function eventSummary(payload: Record<string, unknown>) {
  const summary = payload.summary ?? payload.rationale ?? payload.reason ?? payload.status ?? "";
  if (typeof summary === "string") {
    return summary;
  }
  return JSON.stringify(summary);
}

function translate(value: string) {
  return knownText[value] ?? value;
}

function intentLabel(value: string) {
  return intentLabels[value] ?? value;
}

function toolsLabel(value: unknown) {
  if (!Array.isArray(value) || value.length === 0) {
    return "无";
  }
  return value.join(", ");
}
