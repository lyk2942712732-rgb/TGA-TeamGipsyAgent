import { runtimeApi } from "../../api/runtime";
import type { RuntimeSnapshot } from "../../runtime/event-types";

const roleLabels: Record<string, string> = { main: "主控", recon: "侦察", targeted: "定向", research: "研究" };
const challengeLabels: Record<string, string> = { unknown: "状态未知", active: "挑战进行中", solved: "已完成", blocked: "已阻止", expired: "已过期" };

/** Read-only collaboration summary sourced entirely from the v2 snapshot. */
export function RuntimeStatusStrip({ taskId, snapshot }: { taskId: string; snapshot: RuntimeSnapshot }) {
  const confirmedFlags = snapshot.flags.filter((flag) => Boolean(flag.evidence_artifact_id));
  const coverageGaps = [...new Set(snapshot.subagents.flatMap((item) => item.output?.coverage_gaps ?? []))];

  return <>
    <section className="runtime-summary" aria-label="挑战与协作摘要">
      <span className={`status-badge ${snapshot.challenge.status}`} data-testid="challenge-status">{challengeLabels[snapshot.challenge.status] ?? snapshot.challenge.status}</span>
      {snapshot.challenge.status_reason ? <small>{snapshot.challenge.status_reason}</small> : null}
      <span>活跃 Solver {snapshot.solvers.filter((solver) => ["starting", "running", "waiting"].includes(solver.status)).length}/{snapshot.solvers.length}</span>
    </section>
    {snapshot.solvers.length ? <div className="solver-strip" data-testid="solver-lane" role="list" aria-label="Solver 列表">
      {snapshot.solvers.map((solver) => {
        const active = solver.id === snapshot.session.active_solver_id;
        const parent = solver.parent_solver_id ? snapshot.solvers.find((item) => item.id === solver.parent_solver_id) : undefined;
        return <div key={solver.id} className={`solver-item ${active ? "active" : ""}`} data-testid="solver-item" role="listitem">
          {active ? <span className="solver-active-dot" aria-hidden /> : null}
          <b>{roleLabels[solver.role] ?? solver.role}</b>
          <span className={`status-badge ${solver.status}`}>{solver.status}</span>
          {solver.model_name ? <small title={solver.model_name}>{solver.model_name}</small> : null}
          {parent ? <small>父 Solver：{roleLabels[parent.role] ?? parent.role}</small> : null}
        </div>;
      })}
    </div> : null}
    {snapshot.subagents.length ? <section className="subagent-summary" aria-label="子 Solver 交接">
      <h2>协作路由</h2>
      <div>{snapshot.subagents.map((item) => <article key={item.request.id}>
        <b>{roleLabels[item.request.role] ?? item.request.role}</b><span className={`status-badge ${item.status}`}>{item.status}</span>
        <p>{item.request.objective}</p><small>预算 {item.request.max_actions} actions · 假设 {item.request.hypothesis_ids.length} 条</small>
        {item.output?.next_recommendation ? <small>交接：{item.output.next_recommendation}</small> : null}
      </article>)}</div>
    </section> : null}
    {coverageGaps.length ? <section className="coverage-gaps" aria-label="覆盖缺口"><b>Coverage gaps</b><div>{coverageGaps.map((gap) => <span key={gap}>{gap}</span>)}</div></section> : null}
    {confirmedFlags.length ? <section className="flag-hero" data-testid="flag-hero" role="status">
      <b>已获取 Flag：证据门已通过</b>
      <div>{confirmedFlags.map((flag) => <a key={flag.value} data-testid="artifact-link" href={runtimeApi.artifactUrl(taskId, flag.evidence_artifact_id)} target="_blank" rel="noreferrer"><code>{flag.value}</code> · 查看证据</a>)}</div>
    </section> : null}
  </>;
}
