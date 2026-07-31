import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState";

export type TimelineItem = { id: string; title: ReactNode; timestamp?: ReactNode; description?: ReactNode; meta?: ReactNode; tone?: "neutral" | "info" | "success" | "warning" | "danger" };

export function Timeline({ items, emptyLabel = "暂无时间线事件" }: { items: TimelineItem[]; emptyLabel?: string }) {
  if (!items.length) return <EmptyState label={emptyLabel} />;
  return <ol className="timeline-v2">{items.map((item) => <li key={item.id} className={`tone-${item.tone ?? "neutral"}`}>
    <i aria-hidden="true" /><article><header><strong>{item.title}</strong>{item.timestamp ? <time>{item.timestamp}</time> : null}</header>{item.description ? <p>{item.description}</p> : null}{item.meta ? <footer>{item.meta}</footer> : null}</article>
  </li>)}</ol>;
}
