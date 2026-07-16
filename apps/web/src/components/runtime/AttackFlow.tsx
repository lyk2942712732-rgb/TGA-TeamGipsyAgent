import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { runtimeApi } from "../../api/runtime";
import type {
  Hypothesis,
  MemoryEntry,
  RuntimeAction,
  RuntimeEvent,
  RuntimeSnapshot,
} from "../../runtime/event-types";

type FlowKind = "challenge" | "hypothesis" | "memory" | "manager" | "solver" | "action" | "artifact";
type FlowData = {
  label: ReactNode;
  kind: FlowKind;
  title: string;
  meta: string;
  detail?: string;
  artifactId?: string;
};
type FlowNode = Node<FlowData>;
type Inspector = { title: string; eyebrow: string; meta?: string; detail?: string; artifactId?: string };
type TimelineFilter = "all" | "manager" | "solver" | "tool" | "observer" | "safety";

const roleLabels: Record<string, string> = { main: "Main", recon: "Recon", targeted: "Targeted", research: "Research" };
const filterLabels: Array<[TimelineFilter, string]> = [["all", "All"], ["manager", "Manager"], ["solver", "Solvers"], ["tool", "Tools"], ["observer", "Observer"], ["safety", "Safety"]];

export function AttackFlow({ snapshot, mode = "runtime" }: { snapshot: RuntimeSnapshot; mode?: "runtime" | "replay" }) {
  const events = useMemo(() => [...snapshot.events].sort((a, b) => a.seq - b.seq), [snapshot.events]);
  const [cursor, setCursor] = useState(events.length);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [filter, setFilter] = useState<TimelineFilter>("all");
  const [inspector, setInspector] = useState<Inspector | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);
  const [mobilePane, setMobilePane] = useState<"activity" | "knowledge" | "topology">("activity");
  const activityEnd = useRef<HTMLDivElement | null>(null);
  const atLiveEdge = cursor >= events.length;

  useEffect(() => {
    if (mode === "runtime" && !playing && atLiveEdge) setCursor(events.length);
  }, [atLiveEdge, events.length, mode, playing]);

  useEffect(() => {
    if (!playing) return;
    if (cursor >= events.length) { setPlaying(false); return; }
    const timer = window.setTimeout(() => setCursor((value) => Math.min(events.length, value + 1)), 320 / speed);
    return () => window.clearTimeout(timer);
  }, [cursor, events.length, playing, speed]);

  useEffect(() => {
    if (!atLiveEdge || mobilePane !== "activity") return;
    activityEnd.current?.scrollIntoView({ block: "end" });
  }, [atLiveEdge, events.length, mobilePane]);

  const visibleEvents = events.slice(0, cursor);
  const filteredEvents = visibleEvents.filter((event) => matchesFilter(event, filter));
  const graphs = useMemo(() => ({
    knowledge: buildKnowledgeGraph(snapshot),
    topology: buildTopologyGraph(snapshot, visibleEvents),
  }), [snapshot, visibleEvents]);

  const inspectNode = (_event: React.MouseEvent, node: FlowNode) => setInspector({
    title: node.data.title,
    eyebrow: kindLabel(node.data.kind),
    meta: node.data.meta,
    detail: node.data.detail,
    artifactId: node.data.artifactId,
  });

  return <section className="attack-workbench" aria-label="Attack Flow">
    <div className="workbench-mobile-tabs" role="tablist" aria-label="运行时视图">
      {([['activity', 'Activity'], ['knowledge', 'Knowledge'], ['topology', 'Topology']] as const).map(([value, label]) => <button key={value} role="tab" aria-selected={mobilePane === value} onClick={() => setMobilePane(value)}>{label}</button>)}
    </div>

    <aside className={`activity-pane ${mobilePane === "activity" ? "mobile-active" : ""}`}>
      <header className="workbench-pane-head">
        <div><span className="eyebrow">Live activity</span><h2>Execution timeline</h2></div>
        <span className="pane-count">{filteredEvents.length}/{visibleEvents.length}</span>
      </header>
      <div className="activity-filters" role="tablist" aria-label="事件筛选">
        {filterLabels.map(([value, label]) => <button key={value} role="tab" aria-selected={filter === value} onClick={() => setFilter(value)}>{label}</button>)}
      </div>
      <div className="solver-lanes" data-testid="solver-lane">
        {snapshot.solvers.map((solver) => <button key={solver.id} onClick={() => setInspector(solverInspector(snapshot, solver.id))} title={solver.id}>
          <i className={solver.status} /><span>{roleLabels[solver.role] ?? solver.role}</span><small>{solver.status}</small>
        </button>)}
      </div>
      <div className="activity-scroll" data-testid="runtime-activity">
        {filteredEvents.map((event) => <TimelineRow key={event.id} event={event} snapshot={snapshot} active={event.seq === visibleEvents[visibleEvents.length - 1]?.seq} onOpen={() => setInspector(eventInspector(event, snapshot))} />)}
        {!filteredEvents.length ? <div className="workbench-empty">No events match this view.</div> : null}
        <div ref={activityEnd} />
      </div>
    </aside>

    <div className="workbench-graphs">
      <GraphPane
        className={mobilePane === "knowledge" ? "mobile-active" : ""}
        eyebrow="Session context"
        title="Target & context"
        subtitle={`${snapshot.board.memory.length} context items · ${snapshot.board.hypotheses.length} legacy ideas`}
        nodes={graphs.knowledge.nodes}
        edges={graphs.knowledge.edges}
        onNodeClick={inspectNode}
      />
      <GraphPane
        className={mobilePane === "topology" ? "mobile-active" : ""}
        eyebrow="Agent runtime"
        title="Session & tools"
        subtitle={`${snapshot.solvers.length} solver · ${snapshot.actions.length} tool calls`}
        nodes={graphs.topology.nodes}
        edges={graphs.topology.edges}
        onNodeClick={inspectNode}
        action={<button className="pane-action" onClick={() => setShowEvidence(true)}>Evidence {snapshot.artifacts.length}</button>}
      />
    </div>

    <footer className="workbench-playback">
      <button className="play-button" aria-label={playing ? "暂停回放" : "播放回放"} onClick={() => {
        if (playing) { setPlaying(false); return; }
        if (cursor >= events.length) setCursor(0);
        setPlaying(true);
      }} disabled={!events.length}>{playing ? "Ⅱ" : "▶"}</button>
      <button className={`live-button ${atLiveEdge ? "active" : ""}`} onClick={() => { setPlaying(false); setCursor(events.length); }}>● Live</button>
      <input aria-label="事件回放位置" type="range" min={0} max={Math.max(events.length, 1)} value={Math.min(cursor, events.length)} onChange={(event) => { setPlaying(false); setCursor(Number(event.target.value)); }} />
      <span className="playback-position">{cursor} / {events.length}</span>
      <select aria-label="回放速度" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}><option value={0.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></select>
      <time>{visibleEvents[visibleEvents.length - 1]?.created_at ? formatTime(visibleEvents[visibleEvents.length - 1]!.created_at) : "Start"}</time>
    </footer>

    {inspector ? <InspectorDrawer inspector={inspector} taskId={snapshot.task.id} onClose={() => setInspector(null)} /> : null}
    {showEvidence ? <EvidenceDrawer snapshot={snapshot} onClose={() => setShowEvidence(false)} /> : null}
  </section>;
}

function GraphPane({ eyebrow, title, subtitle, nodes, edges, onNodeClick, action, className = "" }: {
  eyebrow: string; title: string; subtitle: string; nodes: FlowNode[]; edges: Edge[];
  onNodeClick: (event: React.MouseEvent, node: FlowNode) => void; action?: ReactNode; className?: string;
}) {
  return <section className={`graph-pane ${className}`}>
    <header className="workbench-pane-head"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2><small>{subtitle}</small></div>{action}</header>
    <div className="flow-stage">
      {nodes.length ? <ReactFlow nodes={nodes} edges={edges} nodeOrigin={[0, .5]} onNodeClick={onNodeClick} fitView fitViewOptions={{ padding: .22 }} minZoom={.25} maxZoom={1.8} proOptions={{ hideAttribution: true }}>
        <Background color="#d9dde7" gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow> : <div className="workbench-empty">Waiting for runtime state.</div>}
    </div>
  </section>;
}

function TimelineRow({ event, snapshot, active, onOpen }: { event: RuntimeEvent; snapshot: RuntimeSnapshot; active: boolean; onOpen: () => void }) {
  const tone = eventTone(event);
  return <button className={`activity-row ${tone} ${active ? "active" : ""}`} onClick={onOpen} data-timeline-event={event.seq}>
    <span className="activity-seq">#{event.seq}</span><i className="activity-dot" />
    <span className="activity-copy"><b>{eventTitle(event.type)}</b><span>{eventSummary(event, snapshot)}</span><small>{solverLabel(snapshot, event.solver_id)} · {formatTime(event.created_at)}</small></span>
  </button>;
}

function InspectorDrawer({ inspector, taskId, onClose }: { inspector: Inspector; taskId: string; onClose: () => void }) {
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="runtime-drawer" role="dialog" aria-modal="true" aria-label="运行时详情">
      <header><div><span className="eyebrow">{inspector.eyebrow}</span><h2>{inspector.title}</h2></div><button aria-label="关闭详情" onClick={onClose}>×</button></header>
      {inspector.meta ? <div className="drawer-meta">{inspector.meta}</div> : null}
      <div className="drawer-detail">{inspector.detail || "No additional detail was recorded."}</div>
      {inspector.artifactId ? <a className="drawer-link" href={runtimeApi.artifactUrl(taskId, inspector.artifactId)} target="_blank" rel="noreferrer">Open evidence artifact ↗</a> : null}
    </aside>
  </div>;
}

function EvidenceDrawer({ snapshot, onClose }: { snapshot: RuntimeSnapshot; onClose: () => void }) {
  const confirmed = new Set([
    ...snapshot.flags.map((item) => item.evidence_artifact_id),
    ...snapshot.findings.filter((item) => item.status === "confirmed").map((item) => item.evidence_artifact_id ?? ""),
  ]);
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="runtime-drawer evidence-drawer" role="dialog" aria-modal="true" aria-label="证据与结果" data-testid="evidence">
      <header><div><span className="eyebrow">Session output</span><h2>Artifacts & results</h2></div><button aria-label="关闭证据" onClick={onClose}>×</button></header>
      {snapshot.flags.map((flag) => <a className="proof-card" key={flag.value} href={runtimeApi.artifactUrl(snapshot.task.id, flag.evidence_artifact_id)} target="_blank" rel="noreferrer"><b>Solver result</b><code>{flag.value}</code></a>)}
      <div className="evidence-list">{snapshot.artifacts.slice().reverse().map((artifact) => <a key={artifact.id} href={runtimeApi.artifactUrl(snapshot.task.id, artifact.id)} target="_blank" rel="noreferrer"><i className={confirmed.has(artifact.id) ? "confirmed" : ""} /><span><b>{artifact.tool || artifact.kind}</b><small>{artifact.target || artifact.path}</small></span><em>{confirmed.has(artifact.id) ? "proof" : "artifact"}</em></a>)}</div>
      {!snapshot.flags.some((item) => Boolean(item.evidence_artifact_id)) && !snapshot.findings.some((item) => item.status === "confirmed" && item.evidence_artifact_id) ? <div className="workbench-empty compact">No final result yet.</div> : null}
      {!snapshot.artifacts.length ? <div className="workbench-empty">No persisted artifacts yet.</div> : null}
    </aside>
  </div>;
}

function buildKnowledgeGraph(snapshot: RuntimeSnapshot): { nodes: FlowNode[]; edges: Edge[] } {
  // Keep the overview readable at 100% browser zoom. The complete collections
  // remain available through Activity and Evidence; the graph shows the most
  // decision-relevant working set instead of shrinking every node to fit.
  const hypotheses = prioritizedHypotheses(snapshot.board.hypotheses).slice(0, 4);
  const memories = snapshot.board.memory.slice(-4).reverse();
  const span = Math.max(hypotheses.length, memories.length, 1);
  const nodes: FlowNode[] = [node("task", "challenge", 20, (span - 1) * 58, snapshot.task.name, `${snapshot.challenge.status} · ${snapshot.task.mode}`, snapshot.task.target, "running")];
  const edges: Edge[] = [];
  hypotheses.forEach((item, index) => {
    nodes.push(node(`hyp:${item.id}`, "hypothesis", 340, index * 115, item.statement, `${item.attack_class} · ${item.status} · ${Math.round(item.confidence * 100)}%`, hypothesisDetail(item), toneForHypothesis(item), "flow-hypothesis"));
    edges.push(edge(`task-hyp:${item.id}`, "task", `hyp:${item.id}`, toneForHypothesis(item)));
  });
  memories.forEach((item, index) => {
    const artifactId = item.artifact_ids[0];
    nodes.push(node(`mem:${item.id}`, "memory", 690, index * 105, item.content, `${item.kind} · ${sourceLabel(item.source)}`, memoryDetail(item), item.kind === "failure_boundary" ? "failed" : item.kind === "evidence" ? "success" : "neutral", undefined, artifactId));
    const parent = hypotheses.find((hypothesis) => artifactId && hypothesis.evidence_artifact_ids.includes(artifactId));
    edges.push(edge(`memory:${item.id}`, parent ? `hyp:${parent.id}` : "task", `mem:${item.id}`, item.kind === "failure_boundary" ? "failed" : "neutral"));
  });
  return { nodes, edges };
}

function buildTopologyGraph(snapshot: RuntimeSnapshot, events: RuntimeEvent[]): { nodes: FlowNode[]; edges: Edge[] } {
  const nodes: FlowNode[] = [node("manager", "manager", 20, Math.max(0, (snapshot.solvers.length - 1) * 62), "Agent Session", `${snapshot.session.status} · turn ${snapshot.session.turn_count}/${snapshot.session.max_turns}`, snapshot.session.stop_reason || "Hosts the persistent model transcript and lifecycle controls.", "manager")];
  const edges: Edge[] = [];
  snapshot.solvers.forEach((solver, index) => {
    const y = index * 115;
    const solverActions = snapshot.actions.filter((item) => item.solver_id === solver.id || (!item.solver_id && index === 0));
    const latestAction = solverActions[solverActions.length - 1];
    nodes.push(node(`solver:${solver.id}`, "solver", 350, y, `${roleLabels[solver.role] ?? solver.role} solver`, `${solver.status} · ${solver.model_name || "model"}`, solver.id, solver.status === "running" ? "running" : "neutral"));
    edges.push(edge(`manager:${solver.id}`, "manager", `solver:${solver.id}`, solver.status === "running" ? "running" : "neutral"));
    if (latestAction) {
      nodes.push(node(`action:${latestAction.id}`, "action", 700, y, latestAction.capability, `${latestAction.status} · ${latestAction.artifact_ids.length} artifacts`, actionDetail(latestAction), toneForAction(latestAction), "flow-action", latestAction.artifact_ids[0]));
      edges.push(edge(`solver-action:${latestAction.id}`, `solver:${solver.id}`, `action:${latestAction.id}`, toneForAction(latestAction)));
    } else {
      const latest = [...events].reverse().find((event) => event.solver_id === solver.id);
      if (latest) nodes.find((item) => item.id === `solver:${solver.id}`)!.data.detail = `${eventTitle(latest.type)}\n${eventSummary(latest, snapshot)}`;
    }
  });
  if (!snapshot.solvers.length) nodes.push(node("waiting", "solver", 350, 0, "Waiting for solver", "not started", "The Agent Session has not started its Solver yet.", "neutral"));
  return { nodes, edges };
}

function node(id: string, kind: FlowKind, x: number, y: number, title: string, meta: string, detail: string, tone: string, testId?: string, artifactId?: string): FlowNode {
  return {
    id, type: "default", position: { x, y }, sourcePosition: Position.Right, targetPosition: Position.Left,
    className: `runtime-flow-node ${kind} ${tone}`,
    style: { width: 280 },
    data: { kind, title, meta, detail, artifactId, label: <div aria-label={`${title}. ${detail}`} data-testid={testId ?? (kind === "challenge" ? "flow-challenge" : kind === "artifact" ? "flow-artifact" : undefined)}><span>{kindLabel(kind)}</span><b title={title}>{clip(title, 74)}</b><small>{meta}</small></div> },
  };
}

function edge(id: string, source: string, target: string, tone: string): Edge {
  return { id, source, target, type: "smoothstep", className: `runtime-flow-edge ${tone}`, markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 }, animated: tone === "running" };
}

function eventInspector(event: RuntimeEvent, snapshot: RuntimeSnapshot): Inspector {
  const action = event.payload.action_id ? snapshot.actions.find((item) => item.id === event.payload.action_id) : undefined;
  const artifactId = event.payload.evidence_artifact_id || event.payload.artifact_ids?.[0] || action?.artifact_ids[0];
  return { title: eventTitle(event.type), eyebrow: `Event #${event.seq}`, meta: `${solverLabel(snapshot, event.solver_id)} · ${event.created_at}`, detail: eventSummary(event, snapshot), artifactId };
}

function solverInspector(snapshot: RuntimeSnapshot, solverId: string): Inspector {
  const solver = snapshot.solvers.find((item) => item.id === solverId)!;
  const request = snapshot.subagents.find((item) => item.solver_id === solverId);
  return { title: `${roleLabels[solver.role] ?? solver.role} solver`, eyebrow: "Isolated solver session", meta: `${solver.status} · ${solver.model_name || "model not recorded"}`, detail: [solver.id, request?.request.objective, request?.output?.coverage_gaps?.length ? `Coverage gaps: ${request.output.coverage_gaps.join("; ")}` : "", request?.output?.next_recommendation ? `Handoff: ${request.output.next_recommendation}` : ""].filter(Boolean).join("\n\n") };
}

function eventSummary(event: RuntimeEvent, snapshot: RuntimeSnapshot): string {
  const action = event.payload.action_id ? snapshot.actions.find((item) => item.id === event.payload.action_id) : undefined;
  if (event.type === "MESSAGE_START") return "Solver is generating the next turn.";
  if (event.type === "MESSAGE_END") return event.payload.content || "Solver message completed.";
  if (event.type === "TOOL_EXECUTION_START") return `${event.payload.tool_name || "Tool"} started.`;
  if (event.type === "TOOL_EXECUTION_END") return event.payload.summary || `${event.payload.tool_name || "Tool"} · ${event.payload.status || "finished"}`;
  if (event.type === "AGENT_ERROR") return event.payload.message || event.payload.reason || "Agent Session error.";
  if (event.type === "AGENT_FINISHED") return event.payload.summary || "Agent Session finished.";
  if (event.type === "FLAG_FOUND") return event.payload.value || "Solver found a result.";
  if (event.type === "ACTION_PROPOSED") return `${event.payload.capability || action?.capability || "Action"}: 已计划，未执行。${event.payload.rationale || action?.rationale || ""}`;
  if (event.type === "ACTION_APPROVED") return `${action?.capability || event.payload.capability || "Action"} 已通过策略批准，等待执行。`;
  if (event.type === "ACTION_STARTED") return `${action?.capability || "Action"} started${action?.target ? ` · ${action.target}` : ""}`;
  if (event.type === "ACTION_FINISHED") return event.payload.summary || action?.summary || `Finished · ${event.payload.status || action?.status || "unknown"}`;
  if (event.type === "MANAGER_DECISION") return `${event.payload.role || "solver"} · ${event.payload.reason || event.payload.decision || "routing decision"}`;
  if (event.type === "SKILLS_LOADED") return `为本回合加载技能：${(event.payload.skills || []).map((item) => item.name).join("、") || "none"}`;
  if (event.type === "RESULT_REJECTED") return `执行结果未通过校验：${event.payload.reason || "unknown reason"}`;
  if (event.type === "FLAG_CONFIRMED" && !event.payload.evidence_artifact_id) return `确认缺少证据：${event.payload.value || "candidate flag"}`;
  return event.payload.summary || event.payload.reason || event.payload.reminder || event.payload.statement || event.payload.content || event.payload.value || event.payload.status || "Runtime event recorded.";
}

function matchesFilter(event: RuntimeEvent, filter: TimelineFilter) {
  if (filter === "all") return true;
  if (filter === "manager") return event.type.startsWith("SESSION") || event.type === "MANAGER_DECISION";
  if (filter === "solver") return event.type.startsWith("SOLVER") || event.type.startsWith("MESSAGE") || event.type.startsWith("AGENT") || event.type.startsWith("HYPOTHESIS") || event.type === "PLAN_EMPTY" || event.type === "SKILLS_LOADED" || event.type === "MEMORY_UPSERTED";
  if (filter === "tool") return event.type.startsWith("TOOL") || event.type.startsWith("ACTION") || event.type === "RESULT_REJECTED";
  if (filter === "observer") return event.type.startsWith("OBSERVER");
  return event.type === "GATE_REJECTED" || event.type === "RESULT_REJECTED" || event.type.endsWith("FAILED") || Boolean(event.payload.error);
}

function eventTone(event: RuntimeEvent) { if (event.type === "AGENT_ERROR" || event.type === "GATE_REJECTED" || event.type === "RESULT_REJECTED" || event.type.endsWith("FAILED")) return "failed"; if (event.type === "MESSAGE_START" || event.type === "TOOL_EXECUTION_START" || event.type === "ACTION_STARTED") return "running"; if (event.type === "TOOL_EXECUTION_END" || event.type === "AGENT_FINISHED" || event.type === "FLAG_FOUND" || event.type === "ACTION_FINISHED" || event.type.includes("CONFIRMED")) return "success"; if (event.type.startsWith("OBSERVER")) return "observer"; if (event.type === "MANAGER_DECISION") return "manager"; return "neutral"; }
function eventTitle(type: string) { return ({ SESSION_STARTED: "Session started", SESSION_STOPPED: "Session stopped", SESSION_CONTROLLED: "Control accepted", SOLVER_STARTED: "Solver started", SOLVER_STOPPED: "Solver stopped", MESSAGE_START: "Solver thinking", MESSAGE_END: "Solver message", TOOL_EXECUTION_START: "Tool started", TOOL_EXECUTION_END: "Tool result", AGENT_ERROR: "Agent error", AGENT_FINISHED: "Agent finished", FLAG_FOUND: "Result found", HYPOTHESIS_CREATED: "Idea created", HYPOTHESIS_UPDATED: "Idea updated", HYPOTHESIS_STALLED: "Idea stalled", SKILLS_LOADED: "Skills loaded", PLAN_EMPTY: "Empty plan", MANAGER_DECISION: "Manager decision", MEMORY_UPSERTED: "Context updated", ACTION_PROPOSED: "Action proposed", ACTION_APPROVED: "Action approved", ACTION_STARTED: "Tool started", ACTION_FINISHED: "Tool result", RESULT_REJECTED: "Result rejected", OBSERVER_REVIEWED: "Observer review", OBSERVER_FAILED: "Observer failed", GATE_REJECTED: "Gate rejected", FLAG_CONFIRMED: "Flag confirmed", FINDING_CONFIRMED: "Finding confirmed", USER_HINT: "User hint", BOARD_SNAPSHOT: "Board checkpoint" } as Record<string, string>)[type] || type.split("_").join(" ").toLowerCase(); }
function solverLabel(snapshot: RuntimeSnapshot, id?: string | null) { if (!id) return "Manager"; const solver = snapshot.solvers.find((item) => item.id === id); return solver ? roleLabels[solver.role] || solver.role : id.slice(0, 18); }
function prioritizedHypotheses(items: Hypothesis[]) { const rank = { testing: 0, pending: 1, verified: 2, inconclusive: 3, rejected: 4, superseded: 5 }; return [...items].sort((a, b) => rank[a.status] - rank[b.status] || b.confidence - a.confidence); }
function hypothesisDetail(item: Hypothesis) { return [`Entry: ${item.entry_point}`, `Rationale: ${item.rationale || "—"}`, `Next: ${item.next_test || "—"}`, item.last_result ? `${item.status === "rejected" ? "失败边界" : "Latest"}: ${item.last_result}` : "", item.owner_solver_id ? `Owner: ${item.owner_solver_id}` : ""].filter(Boolean).join("\n\n"); }
function memoryDetail(item: MemoryEntry) { return [item.content, `Source: ${item.source}`, item.artifact_ids.length ? `Artifacts: ${item.artifact_ids.join(", ")}` : ""].filter(Boolean).join("\n\n"); }
function actionDetail(item: RuntimeAction) { return [`Target: ${item.target}`, item.rationale ? `Rationale: ${item.rationale}` : "", item.summary ? `Result: ${item.summary}` : "", item.error?.message ? `Error: ${item.error.message}` : ""].filter(Boolean).join("\n\n"); }
function sourceLabel(value: string) { return value.replace(/^solver:/, "").slice(0, 22); }
function toneForHypothesis(item: Hypothesis) { return item.status === "verified" ? "success" : item.status === "rejected" || item.status === "superseded" ? "failed" : item.status === "testing" ? "running" : "neutral"; }
function toneForAction(item: RuntimeAction) { return item.status === "succeeded" ? "success" : ["failed", "blocked", "cancelled"].includes(item.status) ? "failed" : item.status === "running" ? "running" : "neutral"; }
function kindLabel(kind: FlowKind) { return ({ challenge: "TARGET", hypothesis: "LEGACY IDEA", memory: "CONTEXT", manager: "SESSION", solver: "SOLVER", action: "LATEST TOOL", artifact: "ARTIFACT" } as const)[kind]; }
function clip(value: string, limit: number) { const text = value.replace(/\s+/g, " ").trim(); return text.length > limit ? `${text.slice(0, limit)}…` : text; }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
