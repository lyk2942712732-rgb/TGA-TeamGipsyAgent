import { useMemo, useState } from "react";
import type { RuntimeSnapshot } from "../runtime/event-types";

export function SessionReplayPage({ snapshot, onClose }: { snapshot: RuntimeSnapshot; onClose: () => void }) {
  const [index, setIndex] = useState(Math.max(0, snapshot.events.length - 1));
  const event = snapshot.events[index];
  const ordered = useMemo(() => [...snapshot.events].sort((a, b) => a.seq - b.seq), [snapshot.events]);
  return <section className="runtime-page replay-page"><header className="runtime-header"><div><span className="runtime-status paused">replay</span><h2>{snapshot.task.name} 的事件回放</h2><p>仅使用已存储的 AgentEvent；此页面不会执行任何新动作。</p></div><button className="secondary-button" onClick={onClose}>返回实时页</button></header><input aria-label="回放位置" type="range" min="0" max={Math.max(0, ordered.length - 1)} value={index} onChange={(value) => setIndex(Number(value.target.value))} /><div className="replay-controls"><button className="secondary-button" disabled={index === 0} onClick={() => setIndex((value) => value - 1)}>上一步</button><span>{event ? `seq ${event.seq} · ${event.type}` : "暂无事件"}</span><button className="secondary-button" disabled={index >= ordered.length - 1} onClick={() => setIndex((value) => value + 1)}>下一步</button></div>{event ? <article className="replay-event"><h3>{event.type}</h3><pre>{JSON.stringify(event.payload, null, 2)}</pre></article> : null}</section>;
}
