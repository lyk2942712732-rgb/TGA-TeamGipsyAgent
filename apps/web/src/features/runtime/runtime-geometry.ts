import { agentNode, type RuntimeActivity } from "./runtime-activity";
export type GraphBox = { x: number; y: number; width: number; height: number };
export type GraphPoint = { x: number; y: number };
export type GraphRoute = { d: string; label: GraphPoint };
const port = (b: GraphBox, side: "left" | "right" | "top" | "bottom"): GraphPoint => ({ x: side === "left" ? b.x : side === "right" ? b.x + b.width : b.x + b.width / 2, y: side === "top" ? b.y : side === "bottom" ? b.y + b.height : b.y + b.height / 2 });
export function phaseRoute(a: GraphBox, b: GraphBox) { const p = port(a, "right"), t = port(b, "left"), x = (p.x + t.x) / 2; return `M${p.x} ${p.y} H${x} V${t.y} H${t.x}`; }
export function activityRoute(activity: RuntimeActivity, boxes: Record<string, GraphBox>): GraphRoute | null {
  if (!activity.to) return null;
  const a = boxes[activity.from], b = boxes[activity.to]; if (!a || !b || !a.width || !b.width) return null;
  if (activity.from.startsWith("model:") || activity.to.startsWith("model:")) { const p = port(a, "bottom"), t = port(b, "bottom"); return { d: `M${p.x} ${p.y} L${t.x} ${t.y}`, label: p }; }
  if (activity.from === "input") { const p = port(a, "left"), t = port(b, "left"), x = Math.max(5, Math.min(a.x, b.x) - 10); return { d: `M${p.x} ${p.y} H${x} V${t.y} H${t.x}`, label: { x: (p.x + t.x) / 2, y: t.y } }; }
  if (activity.from === agentNode("scout") && activity.to === "blackboard") { const p = port(a, "bottom"), t = port(b, "left"); return { d: `M${p.x} ${p.y} V${t.y} H${t.x}`, label: { x: (p.x + t.x) / 2, y: t.y } }; }
  if (activity.to === agentNode("reporter") || activity.from === agentNode("reporter") && activity.to === "blackboard") { const reversed = activity.from === agentNode("reporter"), p = port(reversed ? b : a, "right"), t = port(reversed ? a : b, "bottom"); return { d: reversed ? `M${t.x} ${t.y} V${p.y} H${p.x}` : `M${p.x} ${p.y} H${t.x} V${t.y}`, label: { x: (p.x + t.x) / 2, y: p.y } }; }
  if (activity.to === "output") { const p = port(a, "top"), t = port(b, "bottom"); return { d: `M${p.x} ${p.y} V${t.y}`, label: { x: p.x, y: (p.y + t.y) / 2 } }; }
  const left = activity.from === "blackboard" && activity.to === agentNode("worker-openai"), side = left ? "left" : "right", p = port(a, side), t = port(b, side), x = left ? Math.min(a.x, b.x) - 22 : Math.max(a.x + a.width, b.x + b.width) + 22;
  return { d: `M${p.x} ${p.y} H${x} V${t.y} H${t.x}`, label: { x, y: (p.y + t.y) / 2 } };
}
export function labelPlacement(point: GraphPoint, text: string, boxes: Record<string, GraphBox>, size: { width: number; height: number }, placed: Array<{ point: GraphPoint; text: string }>): GraphPoint | null {
  if (size.width < 530) return null;
  const widthOf = (value: string) => [...value].reduce((sum, char) => sum + (/[^\x00-\x7F]/.test(char) ? 11 : 6), 14);
  const width = widthOf(text), height = 23, blocks = [...Object.values(boxes), ...placed.map(item => ({ x: item.point.x - widthOf(item.text) / 2, y: item.point.y - height / 2, width: widthOf(item.text), height }))];
  return [point, { x: point.x - width / 2 - 9, y: point.y }, { x: point.x + width / 2 + 9, y: point.y }, { x: point.x, y: point.y - height - 9 }, { x: point.x, y: point.y + height + 9 }].find(p => p.x - width / 2 >= 3 && p.x + width / 2 < size.width - 3 && p.y - height / 2 > 0 && p.y + height / 2 < size.height && !blocks.some(b => p.x + width / 2 > b.x - 3 && p.x - width / 2 < b.x + b.width + 3 && p.y + height / 2 > b.y - 3 && p.y - height / 2 < b.y + b.height + 3)) ?? null;
}
