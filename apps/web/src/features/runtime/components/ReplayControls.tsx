import { orderedEvents } from "../models/selectors";
import type { RuntimeStore } from "../models/types";

export function ReplayControls({ store, seq, onSeq }: { store: RuntimeStore; seq: number; onSeq: (seq: number) => void }) {
  const events = orderedEvents(store);
  const min = Math.max(0, (events[0]?.seq ?? 1) - 1);
  return <section className="replay-controls" aria-label="Replay 时间轴"><div><b>只读 Replay</b><small>{store.legacy ? "v5 Legacy Replay" : "v6 Event Replay"}</small></div><label>事件序号 <output>{seq}</output><input aria-label="回放序列" type="range" min={min} max={Math.max(min, store.latestSeq)} value={Math.max(min, Math.min(seq, store.latestSeq))} onChange={(event) => onSeq(Number(event.target.value))} /></label><span>{events.length} 条持久化事件可回放</span></section>;
}
