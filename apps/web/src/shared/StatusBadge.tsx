import type { ReactNode } from "react";
import { statusDefinition } from "./status";

export function StatusBadge({ value, label, icon }: { value: string; label?: string; icon?: ReactNode }) {
  const definition = statusDefinition(value);
  return <span className={`runtime-status status-badge-v2 tone-${definition.tone} status-${value}`} data-status={value}>
    {icon ?? <i aria-hidden="true" />}
    {label ?? definition.label}
  </span>;
}
