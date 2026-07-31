import { Inbox, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { statusLabel } from "../../shared/status";

export function EmptyState({
  label,
  title,
  description,
  icon: Icon = Inbox,
  action,
}: {
  label?: string;
  title?: string;
  description?: string;
  icon?: LucideIcon;
  action?: ReactNode;
}) {
  if (label && !title && !description && !action) return <div className="empty-state">{label}</div>;
  return <section className="empty-state empty-state-v2" aria-live="polite">
    <span className="empty-state-icon"><Icon size={20} aria-hidden="true" /></span>
    <div><h2>{title ?? label ?? "暂无数据"}</h2>{description ? <p>{description}</p> : null}</div>
    {action ? <div className="empty-state-action">{action}</div> : null}
  </section>;
}

export { statusLabel };
