import { useState } from "react";
import { Check } from "lucide-react";
import type { RuntimeIntent, RuntimeStore } from "../runtime/models/types";
import { StatusBadge } from "../../shared/StatusBadge";
import type { IntentCardView } from "./intent-card";

type View = "kanban" | "graph" | "list";

/**
 * Reference image 05 draws five columns — 待分配 / 运行中 / 等待审批 / 已完成 /
 * 阻塞 — so the runtime's eight Intent states are folded into those buckets and
 * each column takes the reference's accent colour.  Columns show only the live
 * task's Intents, so an empty column reads as empty.
 */
const COLUMNS = [
  { id: "pending", label: "待分配", tone: "neutral", states: ["pending", "assigned"] },
  { id: "running", label: "运行中", tone: "info", states: ["running", "reviewing"] },
  { id: "awaiting_approval", label: "等待审批", tone: "warn", states: ["awaiting_approval"] },
  { id: "completed", label: "已完成", tone: "ok", states: ["completed"] },
  { id: "blocked", label: "阻塞", tone: "danger", states: ["blocked", "failed"] },
] as const;

const STATUS_LABELS: Record<string, string> = { pending: "待处理", assigned: "已分配", running: "运行中", awaiting_approval: "等待审批", reviewing: "审查中", completed: "已完成", blocked: "阻塞", failed: "失败" };
const PRIORITY_LABELS = ["低", "中", "高"];

export function IntentBoard({ store, selectedIntentId, onSelect }: { store: RuntimeStore; selectedIntentId: string | null; onSelect: (intentId: string) => void }) {
  const [view, setView] = useState<View>("kanban");
  const intents = Object.values(store.intentsById).sort((a, b) => b.priority - a.priority || a.intentId.localeCompare(b.intentId));
  // The workspace tab strip above already names this panel, so the board only
  // carries its view switch — a second "Intent 工作项" title reads as a nested page.
  return <section className="intent-board" aria-label="Intent 工作项">
    <header className="intent-board-head"><div className="intent-view-switch" aria-label="工作项视图">{(["kanban", "graph", "list"] as View[]).map((item) => <button key={item} aria-pressed={view === item} onClick={() => setView(item)}>{item === "kanban" ? "Kanban" : item === "graph" ? "依赖图" : "列表"}</button>)}</div></header>
    {view === "kanban" ? <Kanban intents={intents} selectedIntentId={selectedIntentId} onSelect={onSelect} /> : null}
    {view === "graph" ? <DependencyGraph intents={intents} onSelect={onSelect} /> : null}
    {view === "list" ? <IntentTable intents={intents} onSelect={onSelect} /> : null}
  </section>;
}

function Kanban({ intents, selectedIntentId, onSelect }: { intents: RuntimeIntent[]; selectedIntentId: string | null; onSelect: (id: string) => void }) {
  return <div className="intent-kanban" role="region" aria-label="Intent Kanban">{COLUMNS.map((column) => {
    const cards = intents
      .filter((intent) => (column.states as readonly string[]).includes(intent.status))
      .map(toCard);
    const total = cards.length;
    return <section key={column.id} className={`tone-${column.tone}`}>
      <header><b>{column.label}</b><span>{total}</span></header>
      <div className="intent-kanban-cards">
        {cards.length ? cards.map((card) => <IntentCard
          key={card.key}
          card={card}
          selected={card.intentId === selectedIntentId}
          onSelect={onSelect}
        />) : <p className="intent-kanban-empty">暂无</p>}
      </div>
      <footer>{total} 个 Intent</footer>
    </section>;
  })}</div>;
}

function toCard(intent: RuntimeIntent): IntentCardView {
  return {
    key: intent.intentId,
    intentId: intent.intentId,
    title: intent.title || intent.intentId,
    objective: intent.objective,
    status: intent.status,
    priority: PRIORITY_LABELS[Math.min(2, Math.max(0, intent.priority))] ?? String(intent.priority),
    solver: intent.assignedSolverId,
    metrics: [["状态", STATUS_LABELS[intent.status] ?? intent.status]],
    percent: null,
    flag: intent.status === "awaiting_approval" ? "approval"
      : intent.status === "completed" ? "done"
        : ["blocked", "failed"].includes(intent.status) ? "blocked" : null,
  };
}

function IntentCard({ card, selected, onSelect }: { card: IntentCardView; selected: boolean; onSelect: (id: string) => void }) {
  return <button
    type="button"
    className={`intent-card ${selected ? "selected" : ""}`}
    onClick={() => onSelect(card.intentId)}
  >
    <b>{card.title}</b>
    <p>{card.objective}</p>
    <dl>
      <div><dt>优先级</dt><dd className={`priority-${card.priority}`}>{card.priority}</dd></div>
      {card.solver ? <div><dt>Solver</dt><dd>{card.solver}</dd></div> : null}
      {card.metrics.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
    {card.percent === null ? null : <span className="intent-card-progress">
      <i><em style={{ width: `${card.percent}%` }} /></i><b>{card.percent}%</b>
    </span>}
    {card.flag === "approval" ? <span className="ref-chip tone-warn">需审批</span> : null}
    {card.flag === "blocked" ? <span className="ref-chip tone-danger">待解除</span> : null}
    {card.flag === "done" ? <span className="intent-card-done" aria-label="已完成"><Check size={13} /></span> : null}
  </button>;
}

function DependencyGraph({ intents, onSelect }: { intents: RuntimeIntent[]; onSelect: (id: string) => void }) {
  const visible = intents.slice(0, 100);
  return <figure className="intent-dependency-graph" aria-label="Intent 依赖图"><figcaption>{intents.length > visible.length ? `节点已限制为 ${visible.length} 个` : `${visible.length} 个节点`}</figcaption><div>{visible.map((intent) => <button key={intent.intentId} onClick={() => onSelect(intent.intentId)}><b>{intent.title}</b><small>{intent.dependencies.length ? `依赖：${intent.dependencies.join("、")}` : "无依赖"}</small><StatusBadge value={intent.status} /></button>)}</div></figure>;
}

function IntentTable({ intents, onSelect }: { intents: RuntimeIntent[]; onSelect: (id: string) => void }) {
  return <table className="intent-table" aria-label="Intent 列表"><thead><tr><th>Intent</th><th>状态</th><th>Solver</th><th>依赖</th></tr></thead><tbody>{intents.map((intent) => <tr key={intent.intentId} onClick={() => onSelect(intent.intentId)}><th><button onClick={() => onSelect(intent.intentId)}>{intent.title}</button></th><td><StatusBadge value={intent.status} /></td><td>{intent.assignedSolverId ?? "未分配"}</td><td>{intent.dependencies.join("、") || "无"}</td></tr>)}</tbody></table>;
}
