import { selectConfirmedFindings } from "../runtime/models/selectors";
import type { RuntimeStore } from "../runtime/models/types";
import { StatusBadge } from "../../shared/StatusBadge";

export function EvidencePanel({ store }: { store: RuntimeStore }) {
  const claims = Object.values(store.evidenceById);
  const confirmed = selectConfirmedFindings(store);
  return <section aria-labelledby="evidence-title"><header className="runtime-section-title"><div><span>EVIDENCE</span><h3 id="evidence-title">证据与发现</h3></div><small>{claims.length} 条 Claim · {confirmed.length} 项确认发现</small></header>
    <div className="runtime-card-grid">{claims.map((claim) => <article key={claim.claimId}><StatusBadge value={claim.status} /><h4>{claim.statementPreview || claim.claimId}</h4><small>{claim.artifactId}</small></article>)}{confirmed.map((finding) => <article key={finding.findingId}><StatusBadge value={finding.status} /><h4>{finding.title}</h4><p>{finding.descriptionPreview}</p></article>)}</div>
    {!claims.length && !confirmed.length ? <p className="runtime-empty">尚无证据或确认发现</p> : null}
  </section>;
}
