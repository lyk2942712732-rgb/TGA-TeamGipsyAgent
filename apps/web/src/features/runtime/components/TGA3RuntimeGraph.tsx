import { Bot, BrainCircuit, Database, Grab, Lightbulb, Library, UserRound } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import type { TGA3Agent, TGA3BoardEntry, TGA3DialogueMessage, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { stateLabel, stateTone } from "../tga3-view";

type NodeBox = { x: number; y: number; width: number; height: number };
type Activity = { id: string; owner: string; from: string; to?: string; label: string; at: number };
type DragState = { pointerId: number; x: number; y: number; left: number; top: number };

export function TGA3RuntimeGraph({ snapshot }: { snapshot: TGA3RuntimeSnapshot }) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const [boxes, setBoxes] = useState<Record<string, NodeBox>>({});
  const [canvasSize, setCanvasSize] = useState({ width: 1, height: 1 });
  const agents = useMemo(() => orderedAgents(snapshot.agents), [snapshot.agents]);
  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(snapshot.task.state);
  const activities = useMemo(() => terminal ? [] : latestActivities(snapshot), [snapshot, terminal]);
  const activeNodes = useMemo(() => new Set(activities.flatMap((activity) => [activity.from, activity.to].filter((value): value is string => Boolean(value)))), [activities]);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    const viewport = viewportRef.current;
    if (!canvas || !viewport) return;
    const measure = () => {
      const canvasRect = canvas.getBoundingClientRect();
      const next: Record<string, NodeBox> = {};
      canvas.querySelectorAll<HTMLElement>("[data-graph-node]").forEach((element) => {
        const rect = element.getBoundingClientRect();
        const id = element.dataset.graphNode;
        if (!id) return;
        next[id] = { x: rect.left - canvasRect.left + rect.width / 2, y: rect.top - canvasRect.top + rect.height / 2, width: rect.width, height: rect.height };
      });
      setBoxes(next);
      setCanvasSize({ width: canvasRect.width, height: canvasRect.height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    canvas.querySelectorAll<HTMLElement>("[data-graph-node]").forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [agents]);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollLeft = Math.max(0, (viewport.scrollWidth - viewport.clientWidth) / 2);
    viewport.scrollTop = 0;
  }, [snapshot.task.id]);

  const staticEdges: Array<[string, string, "shared" | "model"]> = [
    ["user", "blackboard", "shared"],
    ...agents.flatMap((agent) => [
      [agentNode(agent.agent_id), "blackboard", "shared"],
      [modelNode(agent.agent_id), agentNode(agent.agent_id), "model"],
    ] as Array<[string, string, "shared" | "model"]>),
  ];

  function startDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    event.preventDefault();
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
    viewport.setPointerCapture(event.pointerId);
    viewport.dataset.dragging = "true";
  }
  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const viewport = viewportRef.current; const drag = dragRef.current;
    if (!viewport || !drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    viewport.scrollLeft = drag.left - (event.clientX - drag.x);
    viewport.scrollTop = drag.top - (event.clientY - drag.y);
  }
  function stopDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const viewport = viewportRef.current; const drag = dragRef.current;
    if (!viewport || !drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    delete viewport.dataset.dragging;
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
  }

  return <section className="tga3-runtime-graph" aria-label="实时运行图">
    <header>
      <div><span>LIVE RUNTIME</span><h3>运行图</h3></div>
      <div className="tga3-graph-header-status"><small><Grab size={13} />拖拽画布查看</small><small data-live={!terminal}><i />{terminal ? "运行已结束" : "实时交互"}</small></div>
    </header>
    <div ref={viewportRef} className="tga3-runtime-graph-viewport" onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={stopDrag} onPointerCancel={stopDrag}>
      <div ref={canvasRef} className="tga3-runtime-graph-canvas">
        <svg className="tga3-runtime-graph-edges" viewBox={`0 0 ${canvasSize.width} ${canvasSize.height}`} aria-hidden="true">
          <defs><marker id="tga3-graph-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" /></marker></defs>
          {staticEdges.map(([from, to, kind]) => boxes[from] && boxes[to] ? <line key={`${from}-${to}`} className={`static ${kind}`} {...line(boxes[from], boxes[to])} /> : null)}
          {activities.map((activity) => activity.to && boxes[activity.from] && boxes[activity.to] ? <line key={activity.id} className="active" markerEnd="url(#tga3-graph-arrow)" {...line(boxes[activity.from], boxes[activity.to])} /> : null)}
        </svg>

        <div className="tga3-runtime-graph-grid">
          <GraphNode id="user" className="user" active={activeNodes.has("user")} icon={<UserRound size={22} />} title="用户" meta="任务 · 提示 · Q&A" />
          <GraphNode id="skills" className="skills" active={activeNodes.has("skills")} icon={<Library size={22} />} title="Skills" meta="按需读取能力" />
          <GraphNode id="blackboard" className="blackboard" active={activeNodes.has("blackboard")} icon={<Database size={23} />} title="黑板" meta={`${snapshot.task.blackboard_seq} 条 · ${snapshot.blackboard.filter((entry) => entry.kind === "finding").length} Findings`} />
          <div className="tga3-runtime-graph-lanes">
            {agents.map((agent) => <AgentModelLane key={agent.agent_id} agent={agent} activeNodes={activeNodes} />)}
          </div>
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
  return <article className={`tga3-graph-node ${className}`} data-graph-node={id} data-active={active}><span>{icon}</span><div><b>{title}</b><small>{meta}</small></div></article>;
}

function AgentModelLane({ agent, activeNodes }: { agent: TGA3Agent; activeNodes: Set<string> }) {
  const agentId = agentNode(agent.agent_id); const configuredModelId = modelNode(agent.agent_id);
  const icon = agent.role === "supervisor" ? <Lightbulb size={21} /> : <Bot size={21} />;
  return <section className="tga3-graph-lane">
    <GraphNode id={configuredModelId} className="model" active={activeNodes.has(configuredModelId)} icon={<BrainCircuit size={21} />} title={agent.model_name ?? agent.model_id} meta={agent.provider_id} />
    <article className={`tga3-graph-node agent role-${agent.role}`} data-graph-node={agentId} data-active={activeNodes.has(agentId)}>
      <span>{icon}</span><div><b>{agent.display_name}</b><em className={`tone-${stateTone(agent.actual_state)}`}><i />{stateLabel(agent.actual_state)}</em></div>
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
  return { ...base, from: owner, label: `${message.actor.display_name} ${shortAction(message.text)}` };
}

function activityPosition(activity: Activity, boxes: Record<string, NodeBox>): { x: number; y: number } | null {
  const from = boxes[activity.from];
  if (!from) return null;
  if (!activity.to || !boxes[activity.to]) return { x: from.x + from.width / 2 + 18, y: from.y };
  const to = boxes[activity.to];
  return { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
}

function line(from: NodeBox, to: NodeBox) { return { x1: from.x, y1: from.y, x2: to.x, y2: to.y }; }
function orderedAgents(agents: TGA3Agent[]): TGA3Agent[] { const order: Record<string, number> = { supervisor: 0, "worker-claude": 1, "worker-openai": 2, reporter: 3 }; return [...agents].sort((a, b) => (order[a.agent_id] ?? 9) - (order[b.agent_id] ?? 9)).slice(0, 4); }
function agentNode(agentId: string): string { return `agent:${agentId}`; }
function modelNode(agentId: string): string { return `model:${agentId}`; }
function timestamp(value: string): number { const parsed = Date.parse(value); return Number.isNaN(parsed) ? 0 : parsed; }
function shortAction(value: string): string { const compact = value.replace(/\s+/g, " ").trim(); return compact.length > 34 ? `${compact.slice(0, 34)}…` : compact; }
function boardLabel(kind: TGA3BoardEntry["kind"]): string { return ({ user_prompt: "用户提示", user_file: "用户文件", supervisor_advice: "建议", finding: "Finding", qa: "Q&A", final_candidate: "最终候选" })[kind]; }
