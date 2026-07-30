import type { RuntimeSolver } from "../runtime/models/types";

export function SkillSummary({ solver }: { solver: RuntimeSolver }) {
  const count = Number(solver.skillSnapshot.count ?? 0);
  return <dl className="solver-summary-list"><div><dt>技能摘要</dt><dd>{count ? `${count} 项冻结技能` : "未记录"}</dd></div><div><dt>工具策略</dt><dd>{Number(solver.toolPolicySummary.count ?? 0)} 项能力</dd></div></dl>;
}
