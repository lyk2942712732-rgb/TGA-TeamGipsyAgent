import type { ReactNode } from "react";
import { RiskBadge } from "./RiskBadge";
import { StatusBadge } from "../../shared/StatusBadge";

export function ApprovalCard({ title, status, risk, details, rationale, actions }: { title: string; status: string; risk: string; details: ReactNode; rationale?: ReactNode; actions?: ReactNode }) {
  return <article className="approval-card-v2"><header><h3>{title}</h3><div><StatusBadge value={status} /><RiskBadge value={risk} /></div></header><div>{details}</div>{rationale ? <p>{rationale}</p> : null}{actions ? <footer>{actions}</footer> : null}</article>;
}
