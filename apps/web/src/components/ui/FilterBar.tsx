import { SlidersHorizontal } from "lucide-react";
import type { ReactNode } from "react";

export function FilterBar({ children, resultCount, actions }: { children: ReactNode; resultCount?: number; actions?: ReactNode }) {
  return <section className="filter-bar" aria-label="筛选条件">
    <span className="filter-bar-icon"><SlidersHorizontal size={16} aria-hidden="true" /></span>
    <div className="filter-bar-fields">{children}</div>
    {typeof resultCount === "number" ? <span className="filter-result-count">{resultCount.toLocaleString()} 项</span> : null}
    {actions ? <div className="filter-bar-actions">{actions}</div> : null}
  </section>;
}
