import { selectLatestEventBySolver, selectPendingApprovalsBySolver, selectSolverTree } from "../runtime/models/selectors";
import type { RuntimeStore, SolverTreeNode } from "../runtime/models/types";
import { StatusBadge } from "../../shared/StatusBadge";

export function TeamExplorer({ store, selectedSolverId, onSelect }: { store: RuntimeStore; selectedSolverId: string | null; onSelect: (solverId: string) => void }) {
  const tree = selectSolverTree(store);
  return <nav className="team-explorer" aria-label="团队浏览器">
    <header><span>TEAM</span><h2>Solver 团队</h2><small>{Object.keys(store.solversById).length} 个实例</small></header>
    {tree.length ? <div role="tree" aria-label="Solver 团队">{tree.map((node) => <SolverNode key={node.solver.solverId} store={store} node={node} level={1} selectedSolverId={selectedSolverId} onSelect={onSelect} />)}</div> : <p className="runtime-empty">尚无 Solver</p>}
  </nav>;
}

function SolverNode({ store, node, level, selectedSolverId, onSelect }: { store: RuntimeStore; node: SolverTreeNode; level: number; selectedSolverId: string | null; onSelect: (solverId: string) => void }) {
  const solver = node.solver;
  const latest = selectLatestEventBySolver(store, solver.solverId);
  const solverApprovals = selectPendingApprovalsBySolver(store, solver.solverId);
  const approvals = solverApprovals.length;
  const skillCount = Number(solver.skillSnapshot.count ?? 0);
  return <div className="solver-tree-branch">
    <button type="button" role="treeitem" aria-level={level} aria-selected={selectedSolverId === solver.solverId} aria-expanded={node.children.length ? true : undefined} onClick={() => onSelect(solver.solverId)}>
      <span className="solver-card-copy"><span><b>{solver.solverId}</b><StatusBadge value={solver.status} /></span><small>{solver.orchestrationRole} · {solver.specialties.join(" / ") || "通用"}</small><small>Intent：{solver.assignedIntentId ?? "未分配"}</small><small>最近：{latest?.type ?? "暂无活动"}</small><small>{skillCount} Skills · {solver.budgetUsage.turns ?? 0} 回合 · {solver.budgetUsage.input_tokens ?? 0} Token · {solver.budgetUsage.tool_calls ?? 0} Tools</small>{approvals ? <em>{approvals} 项待审批 · 风险 {solverApprovals.map((item) => item.risk).join("/")}</em> : null}</span>
    </button>
    {node.children.length ? <div role="group">{node.children.map((child) => <SolverNode key={child.solver.solverId} store={store} node={child} level={level + 1} selectedSolverId={selectedSolverId} onSelect={onSelect} />)}</div> : null}
  </div>;
}
