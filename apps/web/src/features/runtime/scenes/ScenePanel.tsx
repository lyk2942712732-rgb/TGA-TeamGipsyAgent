import type { RuntimeStore } from "../models/types";

export function ScenePanel({ store }: { store: RuntimeStore }) {
  const projection = sceneProjection(store);
  return <section className="scene-panel" data-testid="scene-shell"><header className="runtime-section-title"><div><span>SCENE PROJECTION</span><h3>{projection.title}</h3></div><small>后端投影</small></header><div className="scene-projection-grid">{projection.items.map(([label, value]) => <article key={label}><b>{label}</b><span>{value}</span></article>)}</div><p>本视图只整理后端 Snapshot、Evidence 与 Artifact 摘要，不在浏览器推导新的安全结论。</p></section>;
}

function sceneProjection(store: RuntimeStore): { title: string; items: Array<[string, string | number]> } {
  const artifacts = Object.values(store.artifactsById);
  const findings = Object.values(store.findingsById);
  const claims = Object.values(store.evidenceById);
  const raw = record(store.task.raw.mode_config);
  switch (store.task.mode) {
    case "penetration_test": return { title: "渗透测试视图", items: [["Scope", list(raw.scope)], ["Coverage Matrix", `${store.workerResultsById ? Object.keys(store.workerResultsById).length : 0} results`], ["资产", String(artifacts.length)], ["Findings Severity", findings.map((item) => item.severity).join(" / ") || "未投影"], ["Rules of Engagement", list(raw.rules_of_engagement)]] };
    case "incident_response": return { title: "事件响应视图", items: [["Timeline", `${Object.keys(store.eventsBySeq).length} events`], ["IOC", `${claims.length} evidence claims`], ["Evidence Preservation", `${artifacts.length} immutable artifacts`], ["Affected Assets", findings.map((item) => item.target).filter(Boolean).join(" / ") || "未投影"], ["Containment Approval", `${Object.values(store.approvalsById).filter((item) => item.status === "pending").length} pending`]] };
    case "vulnerability_research": return { title: "漏洞研究视图", items: [["Code Coverage", String(raw.coverage ?? "未投影")], ["Crash", String(raw.crash ?? "未投影")], ["PoC", `${artifacts.filter((item) => item.kind.toLowerCase().includes("poc")).length} artifacts`], ["Root Cause", `${findings.length} findings`], ["Exploitability", String(raw.exploitability ?? "未投影")]] };
    case "reverse_engineering": return { title: "逆向分析视图", items: [["Binary Metadata", `${artifacts.length} artifacts`], ["Function / Call Graph", String(raw.call_graph_summary ?? "未投影")], ["Strings / Config", String(raw.strings_summary ?? "未投影")], ["Dynamic Execution", String(raw.dynamic_execution ?? "未投影")], ["Recovered Logic / Protocol", `${Object.keys(store.knowledgeById).length} knowledge items`]] };
    default: return { title: "CTF 视图", items: [["Challenge 分类", String(raw.challenge_type ?? store.modeProjection.challenge.status ?? "未投影")], ["候选 Flag", `${store.modeProjection.flags.length} candidates`], ["Flag Evidence", `${claims.filter((item) => item.status === "confirmed").length} confirmed claims`], ["Flag Validator", String(store.modeProjection.challenge.status ?? "未投影")], ["附件分析", `${store.modeProjection.artifactIndexes.length || artifacts.length} summaries`]] };
  }
}

function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function list(value: unknown): string { return Array.isArray(value) ? value.map(String).join(" / ") || "未投影" : String(value ?? "未投影"); }
