import type { GraphBox, GraphPoint, GraphRoute } from './runtime-geometry';

const middle = (box: GraphBox) => box.y + box.height / 2;
const path = (points: GraphPoint[]): GraphRoute => {
  const clean = points.filter((p, i) => !i || p.x !== points[i - 1].x || p.y !== points[i - 1].y);
  const segments = clean.slice(1).map((b, i) => ({ a: clean[i], b, length: Math.abs(b.x - clean[i].x) + Math.abs(b.y - clean[i].y) }));
  const longest = segments.reduce((a, b) => b.length > a.length ? b : a);
  return { d: clean.map((p, i) => `${i ? 'L' : 'M'}${p.x} ${p.y}`).join(' '), label: { x: (longest.a.x + longest.b.x) / 2, y: (longest.a.y + longest.b.y) / 2 } };
};

// Four parallel Workers share one outer bus. Its rails stay outside both columns,
// so relationships never imply a sequential chain between the four cards.
export function workerBus(boxes: Record<string, GraphBox>) {
  const workers = Object.entries(boxes).filter(([id]) => id.startsWith('agent:worker-'));
  if (workers.length < 3) return null;
  const min = Math.min(...workers.map(([, b]) => b.x)), max = Math.max(...workers.map(([, b]) => b.x + b.width));
  const split = (min + max) / 2;
  if (!workers.some(([, b]) => b.x > split)) return null; // Narrow layouts stack without passive wires.
  const left = min - 14, right = max + 14, top = Math.min(...workers.map(([, b]) => b.y)) - 14;
  const edge = (id: string) => {
    const b = boxes[id], isLeft = b.x + b.width / 2 < split;
    return { x: isLeft ? left : right, y: middle(b), portX: isLeft ? b.x : b.x + b.width };
  };
  const scout = boxes['agent:scout'], reporter = boxes['agent:reporter'];
  const leftBottom = Math.max(top, ...workers.filter(([, b]) => b.x + b.width / 2 < split).map(([, b]) => middle(b)), scout ? middle(scout) : top);
  const rightBottom = Math.max(top, ...workers.filter(([, b]) => b.x + b.width / 2 >= split).map(([, b]) => middle(b)), reporter ? middle(reporter) : top);
  const trunk = `M${left} ${leftBottom} V${top} H${right} V${rightBottom}`;
  const branches = workers.map(([id]) => { const p = edge(id); return `M${p.x} ${p.y} H${p.portX}`; }).join(' ');
  const phases = [scout ? `M${scout.x + scout.width} ${middle(scout)} H${left}` : '', reporter ? `M${right} ${middle(reporter)} H${reporter.x}` : ''].join(' ');
  const knowledge = boxes.knowledge;
  const knowledgeLink = knowledge ? `M${knowledge.x + knowledge.width / 2} ${knowledge.y + knowledge.height} V${top}` : '';
  return { left, right, top, edge, trunk, branches, phases, knowledgeLink };
}

export function workerBusActivity(from: string, to: string, boxes: Record<string, GraphBox>): GraphRoute | null {
  const bus = workerBus(boxes); if (!bus) return null;
  const worker = (id: string) => id.startsWith('agent:worker-') && boxes[id];
  const a = boxes[from], b = boxes[to]; if (!a || !b) return null;
  if (['skills', 'knowledge', 'experience'].includes(from) && worker(to)) {
    const t = bus.edge(to), x = a.x + a.width / 2;
    return { ...path([{ x, y: a.y + a.height }, { x, y: bus.top }, { x: t.x, y: bus.top }, { x: t.x, y: t.y }, { x: t.portX, y: t.y }]),
      label: { x, y: (a.y + a.height + bus.top) / 2 } };
  }
  if ((from === 'blackboard' && worker(to)) || (worker(from) && to === 'blackboard')) {
    const reverse = from === 'blackboard', board = reverse ? a : b, w = bus.edge(reverse ? to : from);
    const x = w.x === bus.left ? board.x + 14 : board.x + board.width - 14, y = board.y - 14;
    const points = [{ x: w.portX, y: w.y }, { x: w.x, y: w.y }, { x: w.x, y }, { x, y }, { x, y: board.y }];
    return { ...path(reverse ? points.reverse() : points), label: reverse ? { x: board.x + board.width / 2, y } : { x: w.x, y: (w.y + y) / 2 } };
  }
  if (worker(from) && worker(to)) {
    const p = bus.edge(from), t = bus.edge(to);
    return path([{ x: p.portX, y: p.y }, { x: p.x, y: p.y }, ...(p.x === t.x ? [] : [{ x: p.x, y: bus.top }, { x: t.x, y: bus.top }]), { x: t.x, y: t.y }, { x: t.portX, y: t.y }]);
  }
  if (worker(from) && to === 'agent:reporter') {
    const p = bus.edge(from), y = middle(b);
    return path([{ x: p.portX, y: p.y }, { x: p.x, y: p.y }, ...(p.x === bus.right ? [] : [{ x: p.x, y: bus.top }, { x: bus.right, y: bus.top }]), { x: bus.right, y }, { x: b.x, y }]);
  }
  return null;
}
