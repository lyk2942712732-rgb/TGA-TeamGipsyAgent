import { useEffect, useRef, useState } from "react";
import type { TGA3RuntimeSnapshot } from "../../runtime/tga3-runtime";
import { runtimeActivityEvents, type RuntimeActivity } from "./runtime-activity";

// Each Agent has its own queue, so parallel events never overwrite one another.
export function useRuntimeActivities(snapshot: TGA3RuntimeSnapshot) {
  const [visible, setVisible] = useState<Record<string, RuntimeActivity>>({});
  const task = useRef(snapshot.task.id), seen = useRef(new Set<string>());
  const slots = useRef<Record<string, RuntimeActivity>>({});
  const queues = useRef(new Map<string, RuntimeActivity[]>());
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const shown = useRef(new Map<string, number>());
  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(snapshot.task.state);
  useEffect(() => {
    const cancel = (owner: string) => { clearTimeout(timers.current.get(owner)); timers.current.delete(owner); };
    const remove = (owner: string) => { cancel(owner); queues.current.delete(owner); shown.current.delete(owner); const next = { ...slots.current }; delete next[owner]; slots.current = next; setVisible(next); };
    const clear = () => { timers.current.forEach(clearTimeout); timers.current.clear(); queues.current.clear(); shown.current.clear(); slots.current = {}; setVisible({}); };
    const schedule = (owner: string, delay: number) => { cancel(owner); timers.current.set(owner, setTimeout(() => { const next = queues.current.get(owner)?.shift(); if (next) show(next); else remove(owner); }, delay)); };
    const show = (activity: RuntimeActivity) => { slots.current = { ...slots.current, [activity.owner]: activity }; setVisible(slots.current); shown.current.set(activity.owner, Date.now()); schedule(activity.owner, queues.current.get(activity.owner)?.length ? 200 : 2600); };
    if (task.current !== snapshot.task.id) { clear(); seen.current.clear(); task.current = snapshot.task.id; }
    const first = seen.current.size === 0;
    for (const event of runtimeActivityEvents(snapshot)) {
      if (seen.current.has(event.id)) continue;
      seen.current.add(event.id);
      if (terminal || first && Date.now() - event.at > 2600) continue;
      if (event.clearOwner) remove(event.clearOwner);
      if (!event.activity) continue;
      const activity = event.activity, current = slots.current[activity.owner];
      if (!current) { show(activity); continue; }
      const elapsed = Date.now() - (shown.current.get(activity.owner) ?? 0), queue = queues.current.get(activity.owner) ?? [];
      if (elapsed >= 200 && !queue.length) show(activity);
      else { queue.push(activity); queues.current.set(activity.owner, queue); schedule(activity.owner, Math.max(0, 200 - elapsed)); }
    }
    if (terminal) clear();
  }, [snapshot, terminal]);
  useEffect(() => () => { timers.current.forEach(clearTimeout); }, []);
  return terminal ? [] : Object.values(visible).sort((a, b) => a.at - b.at);
}
