import type { RuntimeSolver } from "../runtime/models/types";

export function SkillSummary({ solver }: { solver: RuntimeSolver }) {
  const count = Number(solver.skillSnapshot.count ?? 0);
  const hostCount = Array.isArray(solver.capabilityBinding.host_capability_ids) ? solver.capabilityBinding.host_capability_ids.length : 0;
  const kali = solver.capabilityBinding.kali as { capabilities?: unknown[] } | null | undefined;
  return <dl className="solver-summary-list"><div><dt>技能摘要</dt><dd>{count ? `${count} 项冻结技能` : "未记录"}</dd></div><div><dt>能力绑定</dt><dd>{hostCount + (kali?.capabilities?.length ?? 0)} 项能力</dd></div></dl>;
}
