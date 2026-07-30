import { useState } from "react";
import type { RuntimeIntent, RuntimeStore } from "../runtime/models/types";
import { StatusBadge } from "../../shared/StatusBadge";

type View = "kanban" | "graph" | "list";
const STATUSES = ["pending", "assigned", "running", "awaiting_approval", "reviewing", "completed", "blocked", "failed"] as const;
const STATUS_LABELS: Record<string, string> = { pending: "待处理", assigned: "已分配", running: "运行中", awaiting_approval: "等待审批", reviewing: "审查中", completed: "已完成", blocked: "阻塞", failed: "失败" };

export function IntentBoard({ store, selectedIntentId, onSelect }: { store: RuntimeStore; selectedIntentId: string | null; onSelect: (intentId: string) => void }) {
  const [view, setView] = useState<View>("kanban");
  const intents = Object.values(store.intentsById).sort((a, b) => b.priority - a.priority || a.intentId.localeCompare(b.intentId));
  return <section className="intent-board" aria-labelledby="intent-board-title">
    <header className="runtime-section-title"><div><span>WORK ITEMS</span><h3 id="intent-board-title">Intent 工作项</h3></div><div className="intent-view-switch" aria-label="工作项视图">{(["kanban", "graph", "list"] as View[]).map((item) => <button key={item} aria-pressed={view === item} onClick={() => setView(item)}>{item === "kanban" ? "Kanban" : item === "graph" ? "依赖图" : "列表"}</button>)}</div></header>
    {view === "kanban" ? <Kanban intents={intents} selectedIntentId={selectedIntentId} onSelect={onSelect} /> : null}
    {view === "graph" ? <DependencyGraph intents={intents} onSelect={onSelect} /> : null}
    {view === "list" ? <IntentTable intents={intents} onSelect={onSelect} /> : null}
  </section>;
}

function Kanban({ intents, selectedIntentId, onSelect }: { intents: RuntimeIntent[]; selectedIntentId: string | null; onSelect: (id: string) => void }) {
  return <div className="intent-kanban" role="region" aria-label="Intent Kanban">{STATUSES.map((status) => {
    const values = intents.filter((intent) => intent.status === status);
    return <section key={status}><header><b>{STATUS_LABELS[status]}</b><span>{values.length}</span></header>{values.map((intent) => <IntentCard key={intent.intentId} intent={intent} selected={intent.intentId === selectedIntentId} onSelect={onSelect} />)}{!values.length ? <small>暂无</small> : null}</section>;
  })}</div>;
}

function IntentCard({ intent, selected, onSelect }: { intent: RuntimeIntent; selected: boolean; onSelect: (id: string) => void }) {
  return <button className={`intent-card ${selected ? "selected" : ""}`} onClick={() => onSelect(intent.intentId)}><b>{intent.title || intent.intentId}</b><p>{intent.objective}</p><small>{intent.assignedSolverId ?? "未分配"}</small><StatusBadge value={intent.status} /></button>;
}

function DependencyGraph({ intents, onSelect }: { intents: RuntimeIntent[]; onSelect: (id: string) => void }) {
  const visible = intents.slice(0, 100);
  return <figure className="intent-dependency-graph" aria-label="Intent 依赖图"><figcaption>{intents.length > visible.length ? `节点已限制为 ${visible.length} 个` : `${visible.length} 个节点`}</figcaption><div>{visible.map((intent) => <button key={intent.intentId} onClick={() => onSelect(intent.intentId)}><b>{intent.title}</b><small>{intent.dependencies.length ? `依赖：${intent.dependencies.join("、")}` : "无依赖"}</small><StatusBadge value={intent.status} /></button>)}</div></figure>;
}

function IntentTable({ intents, onSelect }: { intents: RuntimeIntent[]; onSelect: (id: string) => void }) {
  return <table className="intent-table" aria-label="Intent 列表"><thead><tr><th>Intent</th><th>状态</th><th>Solver</th><th>依赖</th></tr></thead><tbody>{intents.map((intent) => <tr key={intent.intentId} onClick={() => onSelect(intent.intentId)}><th><button onClick={() => onSelect(intent.intentId)}>{intent.title}</button></th><td><StatusBadge value={intent.status} /></td><td>{intent.assignedSolverId ?? "未分配"}</td><td>{intent.dependencies.join("、") || "无"}</td></tr>)}</tbody></table>;
}
