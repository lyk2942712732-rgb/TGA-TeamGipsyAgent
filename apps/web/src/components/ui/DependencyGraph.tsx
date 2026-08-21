import { ArrowRight } from "lucide-react";
import { StatusBadge } from "../../shared/StatusBadge";
import { EmptyState } from "./EmptyState";

export type DependencyNode = { id: string; label: string; status: string; dependencies: string[] };

export function DependencyGraph({ nodes, onSelect }: { nodes: DependencyNode[]; onSelect?: (id: string) => void }) {
  if (!nodes.length) return <EmptyState label="暂无依赖节点" />;
  return <figure className="dependency-graph" aria-label="依赖关系图"><figcaption>{nodes.length} 个节点</figcaption><div>
    {nodes.map((node) => <button key={node.id} type="button" disabled={!onSelect} onClick={() => onSelect?.(node.id)}>
      <span><b>{node.label}</b><code>{node.id}</code></span><StatusBadge value={node.status} />
      <small>{node.dependencies.length ? <>{node.dependencies.join("、")} <ArrowRight size={11} aria-hidden="true" /></> : "无前置依赖"}</small>
    </button>)}
  </div></figure>;
}
