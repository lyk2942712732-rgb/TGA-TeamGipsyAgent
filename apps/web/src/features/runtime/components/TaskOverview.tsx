import { ScenePanel } from "../scenes/ScenePanel";
import { selectActiveSolvers, selectConfirmedFindings, selectKnowledgeConflicts, selectPendingApprovals } from "../models/selectors";
import type { RuntimeStore } from "../models/types";
import { StatusBadge } from "../../../shared/StatusBadge";

export function TaskOverview({ store, onSelectSolver, onSelectIntent }: { store: RuntimeStore; onSelectSolver: (id: string) => void; onSelectIntent: (id: string) => void }) {
  const solvers = Object.values(store.solversById);
  const intents = Object.values(store.intentsById);
  const completed = intents.filter((intent) => intent.status === "completed").length;
  const blocked = solvers.filter((solver) => ["blocked", "failed", "awaiting_approval"].includes(solver.status));
  const verified = Object.values(store.knowledgeById).filter((item) => item.status === "verified");
  const conflicts = selectKnowledgeConflicts(store);
  const criteria = values(store.globalPlan?.success_criteria ?? record(store.task.raw.mode_config).success_criteria);
  return <div className="task-overview">
    <section className="command-progress" role="region" aria-label="任务总体进度"><header className="runtime-section-title"><div><span>COMMAND STATUS</span><h3>任务总体进度</h3></div><StatusBadge value={store.session.status} /></header><div className="command-metric-grid"><Metric label="活动 Solver" value={selectActiveSolvers(store).length} /><Metric label="完成 Solver" value={solvers.filter((solver) => solver.status === "completed").length} /><Metric label="阻塞 / 审批" value={blocked.length} /><Metric label="待审批" value={selectPendingApprovals(store).length} /></div><p><b>{completed} / {intents.length} Intent 已完成</b></p><progress aria-label="Intent 总体进度" max={Math.max(1, intents.length)} value={completed} /></section>
    <section><header className="runtime-section-title"><div><span>TOPOLOGY</span><h3>团队拓扑</h3></div><small>{solvers.length} Solver</small></header><div className="overview-chip-list">{solvers.map((solver) => <button key={solver.solverId} onClick={() => onSelectSolver(solver.solverId)}><b>{solver.solverId}</b><small>{solver.orchestrationRole}</small><StatusBadge value={solver.status} /></button>)}</div></section>
    <section><header className="runtime-section-title"><div><span>INTENT PROGRESS</span><h3>Intent 进度</h3></div><small>{completed}/{intents.length}</small></header><div className="overview-chip-list">{intents.map((intent) => <button key={intent.intentId} onClick={() => onSelectIntent(intent.intentId)}><b>{intent.title}</b><small>{intent.assignedSolverId ?? "未分配"}</small><StatusBadge value={intent.status} /></button>)}</div></section>
    <section><header className="runtime-section-title"><div><span>VERIFIED KNOWLEDGE</span><h3>关键已确认知识</h3></div><small>{verified.length}</small></header>{verified.length ? <ul>{verified.map((item) => <li key={item.knowledgeId}>{item.contentPreview}</li>)}</ul> : <p className="runtime-empty">暂无已确认知识</p>}</section>
    <section className="overview-risks"><header className="runtime-section-title"><div><span>RISKS</span><h3>风险、阻塞与冲突</h3></div><small>{blocked.length + conflicts.length}</small></header>{blocked.map((solver) => <p key={solver.solverId}><b>{solver.solverId}</b>：{solver.status}</p>)}{conflicts.map((item) => <p key={item.knowledgeId}>{item.contentPreview}</p>)}{!blocked.length && !conflicts.length ? <p className="runtime-empty">当前没有阻塞或知识冲突</p> : null}</section>
    <section><header className="runtime-section-title"><div><span>BUDGET</span><h3>任务预算使用</h3></div></header><div className="command-metric-grid">{Object.entries(store.session.taskBudgetUsage).map(([key, value]) => <Metric key={key} label={key} value={value} />)}</div></section>
    <section><header className="runtime-section-title"><div><span>COMPLETION</span><h3>模式完成条件</h3></div></header>{criteria.length ? <ul>{criteria.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="runtime-empty">后端未投影额外完成条件</p>}</section>
    <ScenePanel store={store} />
    <div className="overview-confirmed-findings"><b>确认发现</b><span>{selectConfirmedFindings(store).length}</span></div>
  </div>;
}

function Metric({ label, value }: { label: string; value: number }) { return <span><small>{label}</small><b>{value.toLocaleString()}</b></span>; }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function values(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
