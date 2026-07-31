import { useState } from "react";
import type { RuntimeIntent, RuntimeStore } from "../runtime/models/types";
import { StatusBadge } from "../../shared/StatusBadge";

type View = "kanban" | "graph" | "timeline";
const COLUMNS = [
  { id: "pending", label: "待分配", states: ["pending", "assigned"] },
  { id: "running", label: "进行中", states: ["running"] },
  { id: "approval", label: "等待审批", states: ["awaiting_approval", "reviewing"] },
  { id: "completed", label: "已完成", states: ["completed"] },
  { id: "blocked", label: "阻塞 / 失败", states: ["blocked", "failed"] },
] as const;

export function IntentBoard({ store, selectedIntentId, onSelect }: { store: RuntimeStore; selectedIntentId: string | null; onSelect: (intentId: string) => void }) {
  const [view, setView] = useState<View>("kanban");
  const intents = Object.values(store.intentsById).sort((a, b) => b.priority - a.priority || a.intentId.localeCompare(b.intentId));
  return <section className="intent-board" aria-labelledby="intent-board-title">
    <header className="runtime-section-title"><div><span>WORK ITEMS</span><h3 id="intent-board-title">Intent 工作项</h3></div><div className="intent-view-switch" aria-label="工作项视图">{(["kanban", "graph", "timeline"] as View[]).map((item) => <button key={item} aria-pressed={view === item} onClick={() => setView(item)}>{item === "kanban" ? "看板" : item === "graph" ? "拓扑" : "时间线"}</button>)}</div></header>
    {view === "kanban" ? <Kanban intents={intents} selectedIntentId={selectedIntentId} onSelect={onSelect} /> : null}
    {view === "graph" ? <DependencyGraph intents={intents} onSelect={onSelect} /> : null}
    {view === "timeline" ? <IntentTimeline intents={intents} onSelect={onSelect} /> : null}
  </section>;
}

function Kanban({ intents, selectedIntentId, onSelect }: { intents: RuntimeIntent[]; selectedIntentId: string | null; onSelect: (id: string) => void }) {
  return <div className="intent-kanban" role="region" aria-label="Intent 看板">{COLUMNS.map((column) => {
    const values = intents.filter((intent) => (column.states as readonly string[]).includes(intent.status));
    return <section key={column.id}><header><b>{column.label}</b><span>{values.length}</span></header>{values.map((intent) => <IntentCard key={intent.intentId} intent={intent} selected={intent.intentId === selectedIntentId} onSelect={onSelect} />)}{!values.length ? <small>暂无</small> : null}</section>;
  })}</div>;
}

function IntentCard({ intent, selected, onSelect }: { intent: RuntimeIntent; selected: boolean; onSelect: (id: string) => void }) {
  return <button className={`intent-card ${selected ? "selected" : ""}`} onClick={() => onSelect(intent.intentId)}><b>{intent.title || intent.intentId}</b><p>{intent.objective}</p><small>{intent.assignedSolverId ?? "未分配"}</small><StatusBadge value={intent.status} /></button>;
}

function DependencyGraph({ intents, onSelect }: { intents: RuntimeIntent[]; onSelect: (id: string) => void }) {
  const visible = intents.slice(0, 100);
  return <figure className="intent-dependency-graph" aria-label="Intent 依赖图"><figcaption>{intents.length > visible.length ? `节点已限制为 ${visible.length} 个` : `${visible.length} 个节点`}</figcaption><div>{visible.map((intent) => <button key={intent.intentId} onClick={() => onSelect(intent.intentId)}><b>{intent.title}</b><small>{intent.dependencies.length ? `依赖：${intent.dependencies.join("、")}` : "无依赖"}</small><StatusBadge value={intent.status} /></button>)}</div></figure>;
}

function IntentTimeline({ intents, onSelect }: { intents: RuntimeIntent[]; onSelect: (id: string) => void }) {
  return <ol className="intent-timeline" aria-label="Intent 时间线">{intents.map((intent) => <li key={intent.intentId}><time>{intent.updatedAt || intent.createdAt || "未记录"}</time><button onClick={() => onSelect(intent.intentId)}><b>{intent.title}</b><small>{intent.assignedSolverId ?? "未分配"} · {intent.dependencies.length ? `依赖 ${intent.dependencies.join("、")}` : "无依赖"}</small></button><StatusBadge value={intent.status} /></li>)}</ol>;
}
