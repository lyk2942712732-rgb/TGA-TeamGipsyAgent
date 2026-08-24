import { Bot, BrainCircuit, Database, Lightbulb, Library, UserRound } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { TGA3Agent, TGA3BoardEntry, TGA3DialogueMessage, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { stateLabel, stateTone } from "../tga3-view";

type GraphPoint = { x: number; y: number };
type GraphFlash = { id: string; from: string; to?: string; label: string; at: number };

export function TGA3RuntimeGraph({ snapshot }: { snapshot: TGA3RuntimeSnapshot }) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const dialogueCursor = useRef<number | null>(null);
  const boardCursor = useRef<number | null>(null);
  const timers = useRef(new Map<string, number>());
  const [points, setPoints] = useState<Record<string, GraphPoint>>({});
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [flashes, setFlashes] = useState<GraphFlash[]>([]);
  const agents = useMemo(() => orderedAgents(snapshot.agents), [snapshot.agents]);
  const models = useMemo(() => distinctModels(agents), [agents]);
  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(snapshot.task.state);

  useEffect(() => {
    dialogueCursor.current = null;
    boardCursor.current = null;
    setFlashes([]);
  }, [snapshot.task.id]);

  useEffect(() => () => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current.clear();
  }, []);

  useEffect(() => {
    const latestDialogue = Math.max(0, ...snapshot.dialogue.map((message) => message.seq));
    const latestBoard = Math.max(0, ...snapshot.blackboard.map((entry) => entry.seq));
    if (dialogueCursor.current === null || boardCursor.current === null) {
      dialogueCursor.current = latestDialogue;
      boardCursor.current = latestBoard;
      return;
    }
    const next = [
      ...snapshot.blackboard.filter((entry) => entry.seq > (boardCursor.current ?? 0)).map(boardFlash),
      ...snapshot.dialogue.filter((message) => message.seq > (dialogueCursor.current ?? 0)).map(dialogueFlash).filter((item): item is GraphFlash => item !== null),
    ].sort((a, b) => a.at - b.at);
    dialogueCursor.current = latestDialogue;
    boardCursor.current = latestBoard;
    if (!next.length) return;
    const latest = next[next.length - 1];
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current.clear();
    setFlashes([latest]);
    const timer = window.setTimeout(() => {
      setFlashes((current) => current.filter((item) => item.id !== latest.id));
      timers.current.delete(latest.id);
    }, 2100);
    timers.current.set(latest.id, timer);
  }, [snapshot.blackboard, snapshot.dialogue]);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const measure = () => {
      const bounds = canvas.getBoundingClientRect();
      const next: Record<string, GraphPoint> = {};
      canvas.querySelectorAll<HTMLElement>("[data-graph-node]").forEach((element) => {
        const rect = element.getBoundingClientRect();
        const id = element.dataset.graphNode;
        if (id) next[id] = { x: rect.left - bounds.left + rect.width / 2, y: rect.top - bounds.top + rect.height / 2 };
      });
      setSize({ width: bounds.width, height: bounds.height });
      setPoints(next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [agents, models]);

  const activeNodes = new Set(flashes.flatMap((flash) => [flash.from, flash.to].filter((value): value is string => Boolean(value))));
  const staticEdges = [
    ["user", "blackboard"],
    ...agents.map((agent) => [agentNode(agent.agent_id), "blackboard"]),
  ] as Array<[string, string]>;

  return <section className="tga3-runtime-graph" aria-label="实时运行图">
    <header><div><span>LIVE RUNTIME</span><h3>运行图</h3></div><small data-live={!terminal}><i />{terminal ? "运行已结束" : "实时交互"}</small></header>
    <div ref={canvasRef} className="tga3-runtime-graph-canvas">
      <svg className="tga3-runtime-graph-edges" viewBox={`0 0 ${size.width || 1} ${size.height || 1}`} aria-hidden="true">
        {staticEdges.map(([from, to]) => points[from] && points[to] ? <line key={`${from}-${to}`} className="static" x1={points[from].x} y1={points[from].y} x2={points[to].x} y2={points[to].y} /> : null)}
        {flashes.map((flash) => flash.to && points[flash.from] && points[flash.to] ? <line key={flash.id} className="active" x1={points[flash.from].x} y1={points[flash.from].y} x2={points[flash.to].x} y2={points[flash.to].y} /> : null)}
      </svg>
      <div className="tga3-runtime-graph-grid">
        <GraphNode id="user" className="user" active={activeNodes.has("user")} icon={<UserRound size={19} />} title="用户" meta="任务 · 提示 · Q&A" />
        <div className="tga3-runtime-graph-agents">{agents.map((agent) => <AgentNode key={agent.agent_id} agent={agent} active={activeNodes.has(agentNode(agent.agent_id))} />)}</div>
        <GraphNode id="skills" className="skills" active={activeNodes.has("skills")} icon={<Library size={19} />} title="Skills" meta="按需读取能力" />
        <GraphNode id="blackboard" className="blackboard" active={activeNodes.has("blackboard")} icon={<Database size={20} />} title="黑板" meta={`${snapshot.task.blackboard_seq} 条 · ${snapshot.blackboard.filter((entry) => entry.kind === "finding").length} Findings`} />
        <GraphNode id="models" className="models" active={activeNodes.has("models")} icon={<BrainCircuit size={20} />} title="大模型" meta={models.join(" · ") || "尚未绑定模型"} />
      </div>
      <div className="tga3-runtime-graph-flashes" aria-live="polite">
        {flashes.map((flash, index) => <span key={flash.id} style={{ "--flash-index": index } as CSSProperties}>{flash.label}</span>)}
        {!flashes.length ? <small>{terminal ? "本次运行交互已完成" : "等待新的动作与交互"}</small> : null}
      </div>
    </div>
  </section>;
}

function GraphNode({ id, className, active, icon, title, meta }: { id: string; className: string; active: boolean; icon: ReactNode; title: string; meta: string }) {
  return <article className={`tga3-graph-node ${className}`} data-graph-node={id} data-active={active}><span>{icon}</span><div><b>{title}</b><small>{meta}</small></div></article>;
}

function AgentNode({ agent, active }: { agent: TGA3Agent; active: boolean }) {
  const icon = agent.role === "supervisor" ? <Lightbulb size={18} /> : <Bot size={18} />;
  return <article className={`tga3-graph-node agent role-${agent.role}`} data-graph-node={agentNode(agent.agent_id)} data-active={active}>
    <span>{icon}</span><div><b>{agent.display_name}</b><small>{agent.provider_id}/{agent.model_name ?? agent.model_id}</small><em className={`tone-${stateTone(agent.actual_state)}`}><i />{stateLabel(agent.actual_state)}</em></div>
  </article>;
}

function boardFlash(entry: TGA3BoardEntry): GraphFlash {
  const from = entry.actor.role === "user" ? "user" : agentNode(entry.actor.agent_id);
  return { id: `board-${entry.seq}`, from, to: "blackboard", label: `${entry.actor.display_name} 写入 ${boardLabel(entry.kind)}`, at: timestamp(entry.created_at) };
}

function dialogueFlash(message: TGA3DialogueMessage): GraphFlash | null {
  const agentId = String(message.payload.agent_id ?? message.actor.agent_id);
  const source = agentNode(agentId);
  const base = { id: `dialogue-${message.seq}`, at: timestamp(message.created_at) };
  if (message.kind === "blackboard_progress") return { ...base, from: "blackboard", to: agentNode("supervisor"), label: "Supervisor 读取黑板更新" };
  if (message.kind === "user_message") return { ...base, from: "user", to: message.channel_agent_id === "supervisor" ? "blackboard" : agentNode(message.channel_agent_id), label: "用户发送提示" };
  if (message.kind === "question") return { ...base, from: source, to: "user", label: `${message.actor.display_name} 请求用户输入` };
  if (message.kind === "model_changed") return { ...base, from: source, to: "models", label: `${message.actor.display_name} 切换模型` };
  if (message.kind === "assistant_delta") return { ...base, from: "models", to: source, label: `${message.actor.display_name} 返回进度` };
  if (message.kind === "agent_status") return { ...base, from: source, label: `${message.actor.display_name}：${String(message.payload.state ?? "状态更新")}` };
  if (message.kind !== "action_started") return null;
  const tool = String(message.payload.tool ?? message.text ?? "执行动作");
  if (/skills?_list|skills?_read|skill/i.test(tool)) return { ...base, from: source, to: "skills", label: `${message.actor.display_name} 读取 Skill` };
  if (/blackboard|artifact_register/i.test(tool)) return { ...base, from: source, to: "blackboard", label: `${message.actor.display_name} ${shortAction(message.text)}` };
  if (/工作周期|work cycle/i.test(message.text)) return { ...base, from: source, to: "models", label: `${message.actor.display_name} 开始推理` };
  return { ...base, from: source, label: `${message.actor.display_name} ${shortAction(message.text)}` };
}

function orderedAgents(agents: TGA3Agent[]): TGA3Agent[] {
  const order: Record<string, number> = { supervisor: 0, "worker-claude": 1, "worker-openai": 2, reporter: 3 };
  return [...agents].sort((a, b) => (order[a.agent_id] ?? 9) - (order[b.agent_id] ?? 9)).slice(0, 4);
}
function distinctModels(agents: TGA3Agent[]): string[] { return [...new Set(agents.map((agent) => `${agent.provider_id}/${agent.model_name ?? agent.model_id}`))]; }
function agentNode(agentId: string): string { return `agent:${agentId}`; }
function timestamp(value: string): number { const parsed = Date.parse(value); return Number.isNaN(parsed) ? 0 : parsed; }
function shortAction(value: string): string { const compact = value.replace(/\s+/g, " ").trim(); return compact.length > 30 ? `${compact.slice(0, 30)}…` : compact; }
function boardLabel(kind: TGA3BoardEntry["kind"]): string { return ({ user_prompt: "用户提示", user_file: "用户文件", supervisor_advice: "建议", finding: "Finding", qa: "Q&A", final_candidate: "最终候选" })[kind]; }
