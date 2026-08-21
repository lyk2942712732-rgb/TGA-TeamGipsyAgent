import { useMemo, useState } from "react";
import { orderedEvents } from "../runtime/models/selectors";
import type { RuntimeEvent, RuntimeStore } from "../runtime/models/types";

type TimelineView = "global" | "lanes";
export function TimelinePanel({ store, solverId, intentId }: { store: RuntimeStore; solverId: string | null; intentId: string | null }) {
  const [view, setView] = useState<TimelineView>("global");
  const [solver, setSolver] = useState(solverId ?? "");
  const [intent, setIntent] = useState(intentId ?? "");
  const [type, setType] = useState("");
  const all = orderedEvents(store);
  const types = [...new Set(all.map((event) => event.type))].sort();
  const filtered = useMemo(() => all.filter((event) => (!solver || event.solverId === solver) && (!intent || event.intentId === intent) && (!type || event.type === type)), [all, solver, intent, type]);
  const visible = filtered.slice(-100);
  const lanes = groupEvents(visible);
  return <section aria-labelledby="timeline-title"><header className="runtime-section-title"><div><span>EVENTS</span><h3 id="timeline-title">活动时间线</h3></div><small>{filtered.length} 条</small></header><div className="timeline-filters"><button aria-pressed={view === "global"} onClick={() => setView("global")}>全局时间线</button><button aria-pressed={view === "lanes"} onClick={() => setView("lanes")}>Solver 泳道</button><label>Solver<select value={solver} onChange={(event) => setSolver(event.target.value)}><option value="">全部</option>{Object.values(store.solversById).map((item) => <option key={item.solverId}>{item.solverId}</option>)}</select></label><label>Intent<select value={intent} onChange={(event) => setIntent(event.target.value)}><option value="">全部</option>{Object.values(store.intentsById).map((item) => <option key={item.intentId} value={item.intentId}>{item.title}</option>)}</select></label><label>事件类型<select value={type} onChange={(event) => setType(event.target.value)}><option value="">全部</option>{types.map((item) => <option key={item}>{item}</option>)}</select></label></div>{filtered.length > visible.length ? <p className="runtime-window-note">为保持流畅，仅窗口化显示最近 {visible.length} 条事件。</p> : null}{view === "global" ? <EventList events={visible} /> : <div className="timeline-lanes">{Object.entries(lanes).map(([lane, events]) => <section key={lane}><h4>{lane}</h4><EventList events={events ?? []} /></section>)}</div>}</section>;
}
function EventList({ events }: { events: RuntimeEvent[] }) { return events.length ? <ol className="runtime-timeline">{events.map((event) => <li key={event.seq}><time>#{event.seq}</time><div><b>{event.type}</b><small>{[event.solverId, event.intentId].filter(Boolean).join(" · ") || "Task"}</small></div></li>)}</ol> : <p className="runtime-empty">当前筛选条件下暂无事件</p>; }
function groupEvents(events: RuntimeEvent[]): Record<string, RuntimeEvent[]> { return events.reduce<Record<string, RuntimeEvent[]>>((groups, event) => { const key = event.solverId ?? "Task"; (groups[key] ??= []).push(event); return groups; }, {}); }
