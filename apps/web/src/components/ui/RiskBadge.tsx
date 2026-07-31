import { ShieldAlert } from "lucide-react";
import { riskDefinition } from "../../shared/status";

export function RiskBadge({ value }: { value: string }) {
  const risk = riskDefinition(value);
  return <span className={`risk-badge-v2 tone-${risk.tone}`} data-risk={value}><ShieldAlert size={12} aria-hidden="true" />{risk.label}</span>;
}
