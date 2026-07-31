import { statusDefinition } from "./status";

export function StatusBadge({ value, label }: { value: string; label?: string }) {
  const definition = statusDefinition(value);
  return <span className={`runtime-status status-badge-v2 tone-${definition.tone} status-${value}`} data-status={value}>
    <i aria-hidden="true" />
    {label ?? definition.label}
  </span>;
}
