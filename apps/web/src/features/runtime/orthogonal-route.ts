import type { GraphBox, GraphPoint, GraphRoute } from "./runtime-geometry";

type Side = "left" | "right" | "top" | "bottom";
function port(b: GraphBox, side: Side): GraphPoint {
  return { x: side === "left" ? b.x : side === "right" ? b.x + b.width : b.x + b.width / 2,
    y: side === "top" ? b.y : side === "bottom" ? b.y + b.height : b.y + b.height / 2 };
}
const distance = (a: GraphPoint, b: GraphPoint) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
// A small visibility search through node gutters keeps staggered lanes connected
// without drawing a wire through another Worker, resource or model label.
export function orthogonalRoute(a: GraphBox, b: GraphBox, boxes: Record<string, GraphBox>, from: Side, to: Side): GraphRoute {
  const p = port(a, from), t = port(b, to);
  const outside = (point: GraphPoint, side: Side) => ({ x: point.x + (side === "left" ? -9 : side === "right" ? 9 : 0), y: point.y + (side === "top" ? -9 : side === "bottom" ? 9 : 0) });
  const start = outside(p, from), end = outside(t, to);
  const obstacles = Object.values(boxes).map(box => { const gap = box === a || box === b ? 0 : 4; return { x: box.x-gap, y: box.y-gap, width: box.width+gap*2, height: box.height+gap*2 }; });
  const clear = (u: GraphPoint, v: GraphPoint) => !obstacles.some(r => u.x === v.x
    ? u.x > r.x && u.x < r.x+r.width && Math.max(u.y,v.y) > r.y && Math.min(u.y,v.y) < r.y+r.height
    : u.y > r.y && u.y < r.y+r.height && Math.max(u.x,v.x) > r.x && Math.min(u.x,v.x) < r.x+r.width);
  const xs = [...new Set([start.x,end.x,...obstacles.flatMap(r => [r.x-6,r.x+r.width+6])])];
  const ys = [...new Set([start.y,end.y,...obstacles.flatMap(r => [r.y-6,r.y+r.height+6])])];
  let best: GraphPoint[] | null = null, cost = Infinity;
  function consider(points: GraphPoint[]) {
    const compact = points.filter((point,index) => !index || distance(point,points[index-1]) > 0);
    const length = compact.slice(1).reduce((sum,point,index) => sum+distance(point,compact[index]),0)+compact.length*4;
    if (length >= cost || !compact.slice(1).every((point,index) => clear(compact[index],point))) return;
    best=compact; cost=length;
  }
  for (const x of xs) consider([p,start,{x,y:start.y},{x,y:end.y},end,t]);
  for (const y of ys) consider([p,start,{x:start.x,y},{x:end.x,y},end,t]);
  for (const x of xs) for (const y of ys) {
    consider([p,start,{x,y:start.y},{x,y},{x:end.x,y},end,t]);
    consider([p,start,{x:start.x,y},{x,y},{x,y:end.y},end,t]);
  }
  const points: GraphPoint[] = best ?? [p,start,{x:start.x,y:end.y},end,t];
  const segments = points.slice(1).map((point,index) => ({ length: distance(point,points[index]), a: points[index], b: point }));
  const longest = segments.reduce((a,b) => b.length > a.length ? b : a);
  return { d: points.map((point,index) => `${index ? "L" : "M"}${point.x} ${point.y}`).join(" "), label: { x: (longest.a.x+longest.b.x)/2, y: (longest.a.y+longest.b.y)/2 } };
}
