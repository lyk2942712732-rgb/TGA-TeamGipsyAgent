import { useState } from "react";
import type { RuntimeStore } from "../runtime/models/types";
import { StatusBadge } from "../../shared/StatusBadge";

type EvidenceTab = "artifacts" | "claims" | "findings";
const TABS: Array<[EvidenceTab, string]> = [["artifacts", "Artifacts"], ["claims", "Evidence Claims"], ["findings", "Findings"]];

export function EvidenceWorkspace({ store }: { store: RuntimeStore }) {
  const [tab, setTab] = useState<EvidenceTab>("artifacts");
  return <section className="evidence-workspace" aria-labelledby="evidence-workspace-title"><header className="runtime-section-title"><div><span>TRACEABILITY</span><h3 id="evidence-workspace-title">证据与发现</h3></div></header><div role="tablist" aria-label="证据类型">{TABS.map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>)}</div><div role="tabpanel">{tab === "artifacts" ? <Artifacts store={store} /> : null}{tab === "claims" ? <Claims store={store} /> : null}{tab === "findings" ? <Findings store={store} /> : null}</div></section>;
}

function Artifacts({ store }: { store: RuntimeStore }) { const values = Object.values(store.artifactsById); return <Cards empty="尚无 Artifact">{values.map((item) => <article key={item.artifactId}><h4>{item.artifactId}</h4><dl><Row label="来源 Intent" value={item.intentId ?? "Task"} /><Row label="来源 Solver" value={solverForIntent(store, item.intentId)} /><Row label="来源 Action" value={actionForArtifact(store, item.artifactId)} /><Row label="类型" value={item.kind} /><Row label="sha256" value={item.sha256} /></dl></article>)}</Cards>; }
function Claims({ store }: { store: RuntimeStore }) { const values = Object.values(store.evidenceById); return <Cards empty="尚无 Evidence Claim">{values.map((item) => <article key={item.claimId}><StatusBadge value={item.status} /><h4>{item.statementPreview}</h4><dl><Row label="来源 Solver" value={item.createdBySolverId ?? "未投影"} /><Row label="Artifact" value={item.artifactId} /><Row label="Evidence locator" value={JSON.stringify(item.locator)} /><Row label="Reviewer" value={item.reviewedBySolverId ?? "未审查"} /></dl></article>)}</Cards>; }
function Findings({ store }: { store: RuntimeStore }) { const values = Object.values(store.findingsById); return <Cards empty="尚无 Finding">{values.map((item) => <article key={item.findingId}><StatusBadge value={item.status} /><h4>{item.title}</h4><p>{item.descriptionPreview}</p><dl><Row label="来源 Solver" value={item.createdBySolverId ?? "未投影"} /><Row label="Evidence Claims" value={item.evidenceClaimIds.join("、") || "无"} /><Row label="Reviewer 状态" value={item.reviewedAt ? "已审查" : "未审查"} /></dl></article>)}</Cards>; }
function Cards({ children, empty }: { children: React.ReactNode[]; empty: string }) { return children.length ? <div className="trace-card-grid">{children}</div> : <p className="runtime-empty">{empty}</p>; }
function Row({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
function solverForIntent(store: RuntimeStore, intentId: string | null): string { return intentId ? store.intentsById[intentId]?.assignedSolverId ?? "未投影" : "Task"; }
function actionForArtifact(store: RuntimeStore, artifactId: string): string { return Object.values(store.actionsById).find((item) => item.artifactIds.includes(artifactId))?.actionId ?? "未投影"; }
