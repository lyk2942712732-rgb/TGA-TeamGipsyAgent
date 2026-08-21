import type { RuntimeStore } from "../runtime/models/types";
import { StatusBadge } from "../../shared/StatusBadge";

export function IntentList({ store, selectedIntentId, onSelect }: { store: RuntimeStore; selectedIntentId: string | null; onSelect: (intentId: string) => void }) {
  const intents = Object.values(store.intentsById).sort((a, b) => b.priority - a.priority || a.intentId.localeCompare(b.intentId));
  return <section aria-labelledby="intent-list-title"><header className="runtime-section-title"><div><span>PLAN</span><h3 id="intent-list-title">Intent 队列</h3></div><small>{intents.length} 项</small></header>
    {intents.length ? <ul className="runtime-entity-list">{intents.map((intent) => <li key={intent.intentId}><button className={intent.intentId === selectedIntentId ? "selected" : ""} onClick={() => onSelect(intent.intentId)}><span><b>{intent.title || intent.intentId}</b><small>{intent.assignedSolverId ?? "尚未分配"}</small></span><StatusBadge value={intent.status} /></button></li>)}</ul> : <p className="runtime-empty">尚无 Intent</p>}
  </section>;
}
