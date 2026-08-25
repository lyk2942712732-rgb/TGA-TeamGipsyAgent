import { Bot, BrainCircuit, Database, Focus, Lightbulb, Library, Minus, Plus, UserRound } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import type { TGA3Agent, TGA3BoardEntry, TGA3DialogueMessage, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { stateLabel, stateTone } from "../tga3-view";

type NodeBox = { x: number; y: number; width: number; height: number };
type Activity = { id: string; owner: string; from: string; to?: string; label: string; at: number };
type DragState = { pointerId: number; x: number; y: number; panX: number; panY: number };

const MIN_ZOOM = 0.75;
const MAX_ZOOM = 1.75;
const ZOOM_STEP = 0.25;

export function TGA3RuntimeGraph({ snapshot }: { snapshot: TGA3RuntimeSnapshot }) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const [boxes, setBoxes] = useState<Record<string, NodeBox>>({});
  const [canvasSize, setCanvasSize] = useState({ width: 1, height: 1 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const agents = useMemo(() => orderedAgents(snapshot.agents), [snapshot.agents]);
  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(snapshot.task.state);
  const activities = useMemo(() => terminal ? [] : latestActivities(snapshot), [snapshot, terminal]);
  const activeNodes = useMemo(() => new Set(activities.flatMap((activity) => [activity.from, activity.to].filter((value): value is string => Boolean(value)))), [activities]);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const measure = () => {
      const canvasRect = canvas.getBoundingClientRect();
      const scaleX = canvas.offsetWidth ? canvasRect.width / canvas.offsetWidth : 1;
      const scaleY = canvas.offsetHeight ? canvasRect.height / canvas.offsetHeight : 1;
      const next: Record<string, NodeBox> = {};
      canvas.querySelectorAll<HTMLElement>("[data-graph-node]").forEach((element) => {
        const rect = element.getBoundingClientRect();
        const id = element.dataset.graphNode;
        if (!id) return;
        next[id] = {
          x: (rect.left - canvasRect.left + rect.width / 2) / scaleX,
          y: (rect.top - canvasRect.top + rect.height / 2) / scaleY,
          width: rect.width / scaleX,
          height: rect.height / scaleY,
        };
      });
      setBoxes(next);
      setCanvasSize({ width: canvas.offsetWidth, height: canvas.offsetHeight });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    canvas.querySelectorAll<HTMLElement>("[data-graph-node]").forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [agents]);

  const staticEdges: Array<[string, string, "shared" | "model"]> = [
    ["user", "blackboard", "shared"],
    ...agents.flatMap((agent) => [
      [agentNode(agent.agent_id), "blackboard", "shared"],
      [modelNode(agent.agent_id), agentNode(agent.agent_id), "model"],
    ] as Array<[string, string, "shared" | "model"]>),
  ];

  function setZoomLevel(next: number) {
    const clamped = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
    setZoom(clamped);
    if (clamped <= 1) setPan({ x: 0, y: 0 });
  }

  function resetView() {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }

  function startDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || zoom <= 1) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    event.preventDefault();
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y };
    viewport.setPointerCapture(event.pointerId);
    viewport.dataset.dragging = "true";
  }

  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    setPan({ x: drag.panX + event.clientX - drag.x, y: drag.panY + event.clientY - drag.y });
  }

  function stopDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const viewport = viewportRef.current;
    const drag = dragRef.current;
    if (!viewport || !drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    delete viewport.dataset.dragging;
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
  }

  return <section className="tga3-runtime-graph" aria-label="实时运行图">
    <header>
      <div><span>LIVE RUNTIME</span><h3>运行图</h3></div>
      <div className="tga3-graph-header-status">
        <div className="tga3-graph-zoom" role="group" aria-label="运行图缩放">
          <button type="button" aria-label="缩小运行图" disabled={zoom <= MIN_ZOOM} onClick={() => setZoomLevel(zoom - ZOOM_STEP)}><Minus size={14} /></button>
          <button type="button" aria-label="还原运行图" title="还原为完整视图" onClick={resetView}><Focus size={14} /><span>{Math.round(zoom * 100)}%</span></button>
          <button type="button" aria-label="放大运行图" disabled={zoom >= MAX_ZOOM} onClick={() => setZoomLevel(zoom + ZOOM_STEP)}><Plus size={14} /></button>
        </div>
        <small data-live={!terminal}><i />{terminal ? "运行已结束" : "实时交互"}</small>
      </div>
    </header>
    <div ref={viewportRef} className="tga3-runtime-graph-viewport" data-pannable={zoom > 1} onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={stopDrag} onPointerCancel={stopDrag}>
      <div ref={canvasRef} className="tga3-runtime-graph-canvas" style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
        <svg className="tga3-runtime-graph-edges" viewBox={`0 0 ${canvasSize.width} ${canvasSize.height}`} aria-hidden="true">
          <defs><marker id="tga3-graph-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" /></marker></defs>
          {staticEdges.map(([from, to, kind]) => boxes[from] && boxes[to] ? <line key={`${from}-${to}`} className={`static ${kind}`} {...line(boxes[from], boxes[to])} /> : null)}
          {activities.map((activity) => {
            const route = activityRoute(activity, boxes);
            return route ? <path key={activity.id} className="active" markerEnd="url(#tga3-graph-arrow)" d={route.d} /> : null;
          })}
        </svg>

        <GraphNode id="user" className="user" active={activeNodes.has("user")} icon={<UserRound size={20} />} title="用户" meta="任务 · 提示 · Q&A" />
        <GraphNode id="skills" className="skills" active={activeNodes.has("skills")} icon={<Library size={20} />} title="Skills" meta="按需读取能力" />
        <GraphNode id="blackboard" className="blackboard" active={activeNodes.has("blackboard")} icon={<Database size={21} />} title="黑板" meta={`${snapshot.task.blackboard_seq} 条 · ${snapshot.blackboard.filter((entry) => entry.kind === "finding").length} Findings`} />
        <div className="tga3-runtime-graph-lanes">
          {agents.map((agent) => <AgentModelLane key={agent.agent_id} agent={agent} activeNodes={activeNodes} />)}
        </div>

        {activities.map((activity) => {
          const position = activityPosition(activity, boxes);
          return position ? <span key={activity.id} aria-live="polite" className={`tga3-graph-activity ${activity.to ? "interaction" : "action"}`} style={{ left: position.x, top: position.y } as CSSProperties}>{activity.label}</span> : null;
        })}
        {!activities.length ? <small className="tga3-runtime-graph-idle">{terminal ? "本次运行交互已完成" : "等待新的动作与交互"}</small> : null}
      </div>
    </div>
  </section>;
}

function GraphNode({ id, className, active, icon, title, meta }: { id: string; className: string; active: boolean; icon: ReactNode; title: string; meta: string }) {
  return <article className={`tga3-graph-node ${className}`} data-graph-node={id} data-active={active}><span>{icon}</span><div><b title={title}>{title}</b><small>{meta}</small></div></article>;
}

function AgentModelLane({ agent, activeNodes }: { agent: TGA3Agent; activeNodes: Set<string> }) {
  const agentId = agentNode(agent.agent_id);
  const configuredModelId = modelNode(agent.agent_id);
  const icon = agent.role === "supervisor" ? <Lightbulb size={19} /> : <Bot size={19} />;
  return <section className="tga3-graph-lane">
    <GraphNode id={configuredModelId} className="model" active={activeNodes.has(configuredModelId)} icon={<BrainCircuit size={19} />} title={agent.model_name ?? agent.model_id} meta={agent.provider_id} />
    <article className={`tga3-graph-node agent role-${agent.role}`} data-graph-node={agentId} data-active={activeNodes.has(agentId)}>
      <span>{icon}</span><div><b title={agent.display_name}>{agent.display_name}</b><em className={`tone-${stateTone(agent.actual_state)}`}><i />{stateLabel(agent.actual_state)}</em></div>
    </article>
  </section>;
}

function latestActivities(snapshot: TGA3RuntimeSnapshot): Activity[] {
  const all = [
    ...snapshot.blackboard.map(boardActivity),
    ...snapshot.dialogue.map(dialogueActivity).filter((activity): activity is Activity => activity !== null),
  ].sort((a, b) => a.at - b.at || a.id.localeCompare(b.id));
  const latest = new Map<string, Activity>();
  all.forEach((activity) => latest.set(activity.owner, activity));
  return [...latest.values()].sort((a, b) => a.at - b.at);
}

function boardActivity(entry: TGA3BoardEntry): Activity {
  const owner = entry.actor.role === "user" ? "user" : agentNode(entry.actor.agent_id);
  return { id: `board-${entry.id}`, owner, from: owner, to: "blackboard", label: `${entry.actor.display_name} 写入 ${boardLabel(entry.kind)}`, at: timestamp(entry.created_at) };
}

function dialogueActivity(message: TGA3DialogueMessage): Activity | null {
  const ownerAgentId = message.actor.agent_id !== "system" ? message.actor.agent_id : String(message.payload.agent_id ?? message.channel_agent_id);
  const owner = ownerAgentId === "user" ? "user" : agentNode(ownerAgentId);
  const model = modelNode(ownerAgentId);
  const base = { id: `dialogue-${message.id}`, owner, at: timestamp(message.created_at) };
  if (message.kind === "blackboard_progress") return { ...base, owner: agentNode("supervisor"), from: "blackboard", to: agentNode("supervisor"), label: "Supervisor 读取黑板更新" };
  if (message.kind === "user_message") return { ...base, owner: "user", from: "user", to: message.channel_agent_id === "supervisor" ? "blackboard" : agentNode(message.channel_agent_id), label: "用户发送提示" };
  if (message.kind === "question") return { ...base, from: owner, to: "user", label: `${message.actor.display_name} 请求用户输入` };
  if (message.kind === "model_changed") return { ...base, from: owner, to: model, label: `${message.actor.display_name} 切换模型` };
  if (message.kind === "assistant_delta") return { ...base, from: model, to: owner, label: `${message.actor.display_name} 收到模型响应` };
  if (message.kind !== "action_started") return null;
  const tool = String(message.payload.tool ?? message.text ?? "执行动作");
  if (/skills?_list|skills?_read|skill/i.test(tool)) return { ...base, from: owner, to: "skills", label: `${message.actor.display_name} 读取 Skill` };
  if (/blackboard|artifact_register/i.test(tool)) return { ...base, from: owner, to: "blackboard", label: `${message.actor.display_name} ${shortAction(message.text)}` };
  if (/工作周期|work cycle/i.test(message.text)) return { ...base, from: owner, to: model, label: `${message.actor.display_name} 请求模型推理` };
  if (isShellTool(tool)) return { ...base, from: owner, label: `${message.actor.display_name} 执行 ${shellCommand(message)}` };
  return { ...base, from: owner, label: `${message.actor.display_name} ${shortAction(message.text)}` };
}

function shellCommand(message: TGA3DialogueMessage): string {
  const payload = message.payload;
  const nested = [payload.input, payload.arguments, payload.args, payload.tool_input, payload.parameters]
    .filter((value): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value));
  const command = [payload.command, payload.cmd, payload.script, ...nested.flatMap((value) => [value.command, value.cmd, value.script]), message.text]
    .find((value) => typeof value === "string" && value.trim());
  if (typeof command !== "string" || /^\s*(shell|bash|shell_exec|execute_command)\s*$/i.test(command)) return "Bash 命令";
  const compact = command.replace(/\s+/g, " ").trim().replace(/^执行\s*(?:Bash\s*)?(?:命令)?\s*[:：]?\s*/i, "");
  return compact.length > 58 ? `${compact.slice(0, 58)}…` : compact;
}

function isShellTool(tool: string): boolean { return /(^|[_.:-])(bash|shell|shell_exec|execute_command|exec_command)([_.:-]|$)/i.test(tool); }

function activityPosition(activity: Activity, boxes: Record<string, NodeBox>): { x: number; y: number } | null {
  const from = boxes[activity.from];
  if (!from) return null;
  if (!activity.to || !boxes[activity.to]) return { x: from.x + from.width / 2 + 12, y: from.y };
  return activityRoute(activity, boxes)?.label ?? null;
}

function activityRoute(activity: Activity, boxes: Record<string, NodeBox>): { d: string; label: { x: number; y: number } } | null {
  if (!activity.to) return null;
  const from = boxes[activity.from];
  const to = boxes[activity.to];
  if (!from || !to) return null;
  const points = [{ x: from.x, y: from.y }];
  const blackboard = boxes.blackboard;
  if (blackboard && activity.from !== "blackboard" && activity.to !== "blackboard" && crossesBox(from, to, blackboard)) {
    const margin = 12;
    const top = blackboard.y - blackboard.height / 2 - margin;
    const bottom = blackboard.y + blackboard.height / 2 + margin;
    const left = blackboard.x - blackboard.width / 2 - margin;
    const right = blackboard.x + blackboard.width / 2 + margin;
    const fromLevel = from.y < blackboard.y ? top : bottom;
    const toLevel = to.y < blackboard.y ? top : bottom;
    const side = (from.x + to.x) / 2 < blackboard.x ? left : right;
    points.push({ x: from.x, y: fromLevel }, { x: side, y: fromLevel }, { x: side, y: toLevel }, { x: to.x, y: toLevel });
  }
  points.push({ x: to.x, y: to.y });
  return { d: points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" "), label: polylineMidpoint(points) };
}

function crossesBox(from: NodeBox, to: NodeBox, box: NodeBox): boolean {
  const left = box.x - box.width / 2;
  const right = box.x + box.width / 2;
  const top = box.y - box.height / 2;
  const bottom = box.y + box.height / 2;
  for (let step = 1; step < 40; step += 1) {
    const ratio = step / 40;
    const x = from.x + (to.x - from.x) * ratio;
    const y = from.y + (to.y - from.y) * ratio;
    if (x > left && x < right && y > top && y < bottom) return true;
  }
  return false;
}

function polylineMidpoint(points: Array<{ x: number; y: number }>): { x: number; y: number } {
  const lengths = points.slice(1).map((point, index) => Math.hypot(point.x - points[index].x, point.y - points[index].y));
  const halfway = lengths.reduce((sum, length) => sum + length, 0) / 2;
  let travelled = 0;
  for (let index = 0; index < lengths.length; index += 1) {
    if (travelled + lengths[index] >= halfway) {
      const ratio = lengths[index] ? (halfway - travelled) / lengths[index] : 0;
      return { x: points[index].x + (points[index + 1].x - points[index].x) * ratio, y: points[index].y + (points[index + 1].y - points[index].y) * ratio };
    }
    travelled += lengths[index];
  }
  return points[points.length - 1];
}

function line(from: NodeBox, to: NodeBox) { return { x1: from.x, y1: from.y, x2: to.x, y2: to.y }; }
function orderedAgents(agents: TGA3Agent[]): TGA3Agent[] { const order: Record<string, number> = { supervisor: 0, "worker-claude": 1, "worker-openai": 2, reporter: 3 }; return [...agents].sort((a, b) => (order[a.agent_id] ?? 9) - (order[b.agent_id] ?? 9)).slice(0, 4); }
function agentNode(agentId: string): string { return `agent:${agentId}`; }
function modelNode(agentId: string): string { return `model:${agentId}`; }
function timestamp(value: string): number { const parsed = Date.parse(value); return Number.isNaN(parsed) ? 0 : parsed; }
function shortAction(value: string): string { const compact = value.replace(/\s+/g, " ").trim(); return compact.length > 34 ? `${compact.slice(0, 34)}…` : compact; }
function boardLabel(kind: TGA3BoardEntry["kind"]): string { return ({ user_prompt: "用户提示", user_file: "用户文件", supervisor_advice: "建议", finding: "Finding", qa: "Q&A", final_candidate: "最终候选" })[kind]; }
