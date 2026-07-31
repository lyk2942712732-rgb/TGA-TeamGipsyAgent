import type { ReactNode } from "react";
import type { BackendCapabilityState } from "../../api/capability-state";

export function ProductPageHeader({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <header className="product-header"><div><h1>{title}</h1><p>{description}</p></div>{action ? <div className="product-header-actions">{action}</div> : null}</header>;
}

export function ProductTabs({ items, active, onChange, label = "页面视图" }: { items: string[]; active: string; onChange: (value: string) => void; label?: string }) {
  return <nav className="product-tabs" aria-label={label}>{items.map((item) => <button key={item} className={item === active ? "active" : ""} onClick={() => onChange(item)}>{item}</button>)}</nav>;
}

export function CapabilityNotice({ state, reason }: { state: BackendCapabilityState; reason: string }) {
  if (state === "available") return null;
  return <div className={`capability-notice ${state}`} role="status"><strong>{state === "read_only" ? "只读能力" : "暂不支持"}</strong><span>{reason}</span></div>;
}

export function DisabledAction({ children, reason, className = "secondary-button" }: { children: ReactNode; reason: string; className?: string }) {
  return <button className={className} disabled title={reason} aria-label={`${String(children)}：${reason}`}>{children}</button>;
}

export function ProductEmpty({ title, description }: { title: string; description: string }) {
  return <div className="product-empty"><strong>{title}</strong><p>{description}</p></div>;
}

export function ProductTable({ headers, children, label }: { headers: string[]; children: ReactNode; label: string }) {
  return <div className="product-table-wrap"><table className="product-table" aria-label={label}><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}

export function DefinitionList({ rows }: { rows: Array<[string, ReactNode]> }) {
  return <dl className="definition-list">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || "-"}</dd></div>)}</dl>;
}

export function Chip({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "success" | "warning" | "danger" | "info" }) {
  return <span className={`product-chip ${tone}`}>{children}</span>;
}
