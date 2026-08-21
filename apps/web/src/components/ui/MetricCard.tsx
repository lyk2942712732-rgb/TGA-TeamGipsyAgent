import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function MetricCard({ label, value, detail, icon: Icon, tone = "neutral" }: { label: string; value: ReactNode; detail?: ReactNode; icon?: LucideIcon; tone?: "neutral" | "info" | "success" | "warning" | "danger" }) {
  return <article className={`metric-card-v2 tone-${tone}`}>
    <header>{Icon ? <Icon size={16} aria-hidden="true" /> : null}<span>{label}</span></header>
    <strong>{value}</strong>{detail ? <small>{detail}</small> : null}
  </article>;
}
