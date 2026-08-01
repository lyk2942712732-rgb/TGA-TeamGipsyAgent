import { Users } from "lucide-react";
import { selectLatestEventBySolver, selectPendingApprovalsBySolver, selectSolverTree } from "../runtime/models/selectors";
import type { RuntimeStore, SolverTreeNode } from "../runtime/models/types";
import { solverShortName } from "../../i18n/catalog";
import { statusDefinition } from "../../shared/status";

/**
 * Reference image 05's Team Explorer rail: a card whose rows are an initialled
 * avatar, the Solver's short role name, its operator line and a coloured status
 * dot, with 查看团队详情 pinned to the bottom.
 *
 * The rail lists exactly the Solvers the task has spawned.  A task early in its
 * lifecycle therefore shows a short rail rather than a padded roster.
 */

/** Role-tinted avatars, matching the reference's per-Solver colours. */
const ROLE_TONES: Record<string, string> = {
  supervisor: "tone-amber", worker: "tone-info", reviewer: "tone-violet", reporter: "tone-danger",
};

export function TeamExplorer({ store, selectedSolverId, onSelect, onDetails }: {
  store: RuntimeStore;
  selectedSolverId: string | null;
  onSelect: (solverId: string) => void;
  onDetails?: () => void;
}) {
  const tree = selectSolverTree(store);

  return <nav className="team-explorer" aria-label="团队浏览器">
    <header><h2>Team Explorer</h2><small>{Object.keys(store.solversById).length} 个实例</small></header>
    {tree.length
      ? <div className="team-explorer-tree" role="tree" aria-label="Solver 团队">
        {tree.map((node) => <SolverNode key={node.solver.solverId} store={store} node={node} level={1} selectedSolverId={selectedSolverId} onSelect={onSelect} />)}
      </div>
      : <p className="runtime-empty">尚无 Solver</p>}
    <footer>
      <button type="button" className="ref-secondary-button" onClick={onDetails}>
        <Users size={14} aria-hidden="true" />查看团队详情
      </button>
    </footer>
  </nav>;
}

function SolverNode({ store, node, level, selectedSolverId, onSelect }: { store: RuntimeStore; node: SolverTreeNode; level: number; selectedSolverId: string | null; onSelect: (solverId: string) => void }) {
  const solver = node.solver;
  const latest = selectLatestEventBySolver(store, solver.solverId);
  const solverApprovals = selectPendingApprovalsBySolver(store, solver.solverId);
  const approvals = solverApprovals.length;
  const skillCount = Number(solver.skillSnapshot.count ?? 0);
  const status = statusDefinition(solver.status);
  return <div className="solver-tree-branch">
    <button
      type="button"
      role="treeitem"
      aria-level={level}
      aria-selected={selectedSolverId === solver.solverId}
      aria-expanded={node.children.length ? true : undefined}
      onClick={() => onSelect(solver.solverId)}
    >
      <span className={`solver-avatar ${ROLE_TONES[solver.orchestrationRole] ?? "tone-muted"}`} aria-hidden="true">
        {solverShortName(solver.definitionId, solver.orchestrationRole).charAt(0).toUpperCase()}
      </span>
      <span className="solver-card-copy">
        <b>{solverShortName(solver.definitionId, solver.orchestrationRole)}</b>
        <small>{solver.solverId}</small>
        {/* One clipped line: the reference row is three lines tall, so the live
            counters share a line instead of stacking four deep. */}
        <small className="solver-card-meta">
          {solver.orchestrationRole} · {solver.specialties.join(" / ") || "通用"}
          {" · "}Intent：{solver.assignedIntentId ?? "未分配"}
          {" · "}{skillCount} Skills · {solver.budgetUsage.input_tokens ?? 0} Token
          {" · "}{solver.budgetUsage.tool_calls ?? 0} Tools · 最近：{latest?.type ?? "暂无活动"}
        </small>
        <span className={`solver-card-status tone-${status.tone}`}><i aria-hidden="true" />{status.label}</span>
        {approvals ? <em>{approvals} 项待审批 · 风险 {solverApprovals.map((item) => item.risk).join("/")}</em> : null}
      </span>
    </button>
    {node.children.length ? <div role="group">{node.children.map((child) => <SolverNode key={child.solver.solverId} store={store} node={child} level={level + 1} selectedSolverId={selectedSolverId} onSelect={onSelect} />)}</div> : null}
  </div>;
}
