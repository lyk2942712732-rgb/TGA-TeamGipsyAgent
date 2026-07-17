import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { runtimeApi } from "../../api/runtime";
import type { ArtifactPreviewResponse } from "../../runtime/api-v2";
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
  actionId?: string;
  flagValue?: string;
  requestPayload?: string;
};
type FlowNode = Node<FlowData>;
type Inspector = { title: string; eyebrow: string; meta?: string; detail?: string; artifactId?: string; actionId?: string; flagValue?: string; requestPayload?: string };
type TimelineFilter = "all" | "manager" | "solver" | "tool" | "observer" | "safety";

const roleLabels: Record<string, string> = { main: "Main", recon: "Recon", targeted: "Targeted", research: "Research" };
const filterLabels: Array<[TimelineFilter, string]> = [["all", "All"], ["manager", "Manager"], ["solver", "Solvers"], ["tool", "Tools"], ["observer", "Observer"], ["safety", "Safety"]];

export function AttackFlow({ snapshot, mode = "runtime" }: { snapshot: RuntimeSnapshot; mode?: "runtime" | "replay" }) {
  const events = useMemo(() => [...snapshot.events].sort((a, b) => a.seq - b.seq), [snapshot.events]);
  const [cursor, setCursor] = useState(events.length);
  const [playing, setPlaying] = useState(false);
  const [followLive, setFollowLive] = useState(mode === "runtime");
  const [speed, setSpeed] = useState(1);
  const [filter, setFilter] = useState<TimelineFilter>("all");
  const [inspector, setInspector] = useState<Inspector | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);
  const [mobilePane, setMobilePane] = useState<"activity" | "knowledge" | "topology">("activity");
  const activityEnd = useRef<HTMLDivElement | null>(null);
  const atLiveEdge = cursor >= events.length;

  useEffect(() => {
    if (mode === "runtime" && followLive && !playing) setCursor(events.length);
  }, [events.length, followLive, mode, playing]);

  useEffect(() => {
    setFollowLive(mode === "runtime");
    if (mode === "runtime") setCursor(events.length);
  }, [mode]);

  useEffect(() => {
    if (!playing) return;
    if (cursor >= events.length) { setPlaying(false); return; }
    const timer = window.setTimeout(() => setCursor((value) => Math.min(events.length, value + 1)), 320 / speed);
    return () => window.clearTimeout(timer);
  }, [cursor, events.length, playing, speed]);

  useEffect(() => {
    if (!followLive || !atLiveEdge || mobilePane !== "activity") return;
    activityEnd.current?.scrollIntoView({ block: "end" });
  }, [atLiveEdge, events.length, followLive, mobilePane]);

  const visibleEvents = events.slice(0, cursor);
  const filteredEvents = visibleEvents.filter((event) => matchesFilter(event, filter));
  const playbackSnapshot = useMemo(
    () => projectSnapshotAtCursor(snapshot, visibleEvents, cursor >= events.length),
    [cursor, events.length, snapshot, visibleEvents],
  );
  const graphs = useMemo(() => ({
    knowledge: buildKnowledgeGraph(playbackSnapshot),
    topology: buildTopologyGraph(playbackSnapshot, visibleEvents),
  }), [playbackSnapshot, visibleEvents]);

  const inspectNode = (_event: React.MouseEvent, node: FlowNode) => setInspector({
    title: node.data.title,
    eyebrow: kindLabel(node.data.kind),
    meta: node.data.meta,
    detail: node.data.detail,
    artifactId: node.data.artifactId,
    actionId: node.data.actionId,
    flagValue: node.data.flagValue,
    requestPayload: node.data.requestPayload,
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
        {playbackSnapshot.solvers.map((solver) => <button key={solver.id} onClick={() => setInspector(solverInspector(playbackSnapshot, solver.id))} title={solver.id}>
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
        subtitle={`${playbackSnapshot.board.memory.length} context items · ${playbackSnapshot.board.hypotheses.length} legacy ideas`}
        nodes={graphs.knowledge.nodes}
        edges={graphs.knowledge.edges}
        onNodeClick={inspectNode}
      />
      <GraphPane
        className={mobilePane === "topology" ? "mobile-active" : ""}
        eyebrow="Agent runtime"
        title="Session & tools"
        subtitle={toolCallSummary(playbackSnapshot)}
        nodes={graphs.topology.nodes}
        edges={graphs.topology.edges}
        onNodeClick={inspectNode}
        action={<button className="pane-action" onClick={() => setShowEvidence(true)}>Evidence {playbackSnapshot.artifacts.length}</button>}
      />
    </div>

    <footer className="workbench-playback">
      <button className="play-button" aria-label={playing ? "暂停回放" : "播放回放"} onClick={() => {
        if (playing) { setPlaying(false); return; }
        setFollowLive(false);
        if (cursor >= events.length) setCursor(0);
        setPlaying(true);
      }} disabled={!events.length}>{playing ? "Ⅱ" : "▶"}</button>
      <button className={`live-button ${followLive && atLiveEdge ? "active" : ""}`} onClick={() => { setPlaying(false); setFollowLive(true); setCursor(events.length); }}>● Live</button>
      <input aria-label="事件回放位置" type="range" min={0} max={Math.max(events.length, 1)} value={Math.min(cursor, events.length)} onChange={(event) => { setPlaying(false); setFollowLive(false); setCursor(Number(event.target.value)); }} />
      <span className="playback-position">{cursor} / {events.length}</span>
      <select aria-label="回放速度" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}><option value={0.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></select>
      <time>{visibleEvents[visibleEvents.length - 1]?.created_at ? formatTime(visibleEvents[visibleEvents.length - 1]!.created_at) : "Start"}</time>
    </footer>

    {inspector ? <InspectorDrawer inspector={inspector} taskId={snapshot.task.id} onClose={() => setInspector(null)} /> : null}
    {showEvidence ? <EvidenceDrawer snapshot={playbackSnapshot} onClose={() => setShowEvidence(false)} /> : null}
  </section>;
}

function GraphPane({ eyebrow, title, subtitle, nodes, edges, onNodeClick, action, className = "" }: {
  eyebrow: string; title: string; subtitle: string; nodes: FlowNode[]; edges: Edge[];
  onNodeClick: (event: React.MouseEvent, node: FlowNode) => void; action?: ReactNode; className?: string;
}) {
  return <section className={`graph-pane ${className}`}>
    <header className="workbench-pane-head"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2><small>{subtitle}</small></div>{action}</header>
    <div className="flow-stage">
      {nodes.length ? <ReactFlowProvider><FlowCanvas nodes={nodes} edges={edges} onNodeClick={onNodeClick} /></ReactFlowProvider> : <div className="workbench-empty">Waiting for runtime state.</div>}
    </div>
  </section>;
}

function FlowCanvas({ nodes, edges, onNodeClick }: { nodes: FlowNode[]; edges: Edge[]; onNodeClick: (event: React.MouseEvent, node: FlowNode) => void }) {
  const initialized = useNodesInitialized();
  const { fitView } = useReactFlow();
  const layoutKey = nodes.map((item) => `${item.id}:${item.position.x}:${item.position.y}`).join("|");

  useEffect(() => {
    if (!initialized) return;
    const frame = window.requestAnimationFrame(() => { void fitView({ padding: .22, duration: 180 }); });
    return () => window.cancelAnimationFrame(frame);
  }, [fitView, initialized, layoutKey]);

  return <ReactFlow nodes={nodes} edges={edges} nodeOrigin={[0, .5]} onNodeClick={onNodeClick} fitView fitViewOptions={{ padding: .22 }} minZoom={.18} maxZoom={1.8} proOptions={{ hideAttribution: true }}>
    <Background color="#d9dde7" gap={20} size={1} />
    <Controls showInteractive={false} />
  </ReactFlow>;
}

function TimelineRow({ event, snapshot, active, onOpen }: { event: RuntimeEvent; snapshot: RuntimeSnapshot; active: boolean; onOpen: () => void }) {
  const tone = eventTone(event);
  return <button className={`activity-row ${tone} ${active ? "active" : ""}`} onClick={onOpen} data-timeline-event={event.seq}>
    <span className="activity-seq">#{event.seq}</span><i className="activity-dot" />
    <span className="activity-copy"><b>{eventTitle(event.type)}</b><span>{eventSummary(event, snapshot)}</span><small>{solverLabel(snapshot, event.solver_id)} · {formatTime(event.created_at)}</small></span>
  </button>;
}

function InspectorDrawer({ inspector, taskId, onClose }: { inspector: Inspector; taskId: string; onClose: () => void }) {
  const [artifact, setArtifact] = useState<ArtifactPreviewResponse | null>(null);
  const [artifactError, setArtifactError] = useState("");
  const parsedArtifact = useMemo(() => parseArtifactPreview(artifact?.preview), [artifact?.preview]);

  useEffect(() => {
    let current = true;
    setArtifact(null);
    setArtifactError("");
    if (!inspector.artifactId) return () => { current = false; };
    runtimeApi.artifact(taskId, inspector.artifactId).then((value) => {
      if (current) setArtifact(value);
    }).catch((error: unknown) => {
      if (current) setArtifactError(error instanceof Error ? error.message : "Evidence could not be loaded.");
    });
    return () => { current = false; };
  }, [inspector.artifactId, taskId]);

  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="runtime-drawer" role="dialog" aria-modal="true" aria-label="运行时详情">
      <header><div><span className="eyebrow">{inspector.eyebrow}</span><h2>{inspector.title}</h2></div><button aria-label="关闭详情" onClick={onClose}>×</button></header>
      {inspector.flagValue ? <div className="flag-proof" data-testid="flag-proof"><b>🏆 FLAG FOUND</b><code>{inspector.flagValue}</code><span>This tool call produced the confirmed evidence.</span></div> : null}
      {inspector.meta ? <div className="drawer-meta">{inspector.meta}</div> : null}
      <div className="drawer-detail">{inspector.detail || "No additional detail was recorded."}</div>
      {inspector.artifactId && !artifact && !artifactError ? <div className="artifact-loading">Loading formatted evidence…</div> : null}
      {artifactError ? <div className="artifact-error">Formatted evidence unavailable: {artifactError}</div> : null}
      {parsedArtifact ? <FormattedArtifact data={parsedArtifact} flagValue={inspector.flagValue} requestPayload={inspector.requestPayload} /> : null}
      {inspector.artifactId ? <a className="drawer-link secondary" href={runtimeApi.artifactUrl(taskId, inspector.artifactId)} target="_blank" rel="noreferrer">Open raw JSON ↗</a> : null}
    </aside>
  </div>;
}

function FormattedArtifact({ data, flagValue, requestPayload }: { data: Record<string, unknown>; flagValue?: string; requestPayload?: string }) {
  const method = textValue(data.method) || "REQUEST";
  const requestedUrl = textValue(data.requested_url) || textValue(data.final_url) || "Unknown target";
  const requestBody = formatRequestPayload(textValue(data.body) || textValue(data.request_body)) || requestPayload || "";
  const responseBody = readableResponseBody(textValue(data.body_excerpt) || textValue(data.body_text) || textValue(data.response_body));
  const status = textValue(data.status) || "unknown";
  const duration = textValue(data.duration_ms);
  const contentType = textValue(data.content_type);
  const requestHeaders = recordValue(data.request_headers);
  const responseHeaders = recordValue(data.response_headers);

  return <div className="artifact-preview" data-testid="formatted-artifact">
    <div className="artifact-preview-title"><span>Evidence preview</span><b>Readable request & response</b></div>
    <section className="artifact-section">
      <h3>Request</h3>
      <dl><div><dt>Method</dt><dd>{method}</dd></div><div><dt>URL</dt><dd>{requestedUrl}</dd></div></dl>
      {requestBody ? <><h4>Payload</h4><pre>{requestBody}</pre></> : null}
    </section>
    <section className={`artifact-section response ${flagValue ? "has-flag" : ""}`}>
      <h3>{flagValue ? "🏆 Response that revealed the flag" : "Response"}</h3>
      <dl><div><dt>Status</dt><dd>HTTP {status}</dd></div>{duration ? <div><dt>Duration</dt><dd>{duration} ms</dd></div> : null}{contentType ? <div><dt>Type</dt><dd>{contentType}</dd></div> : null}</dl>
      {responseBody ? <><h4>Relevant body text</h4><pre className={flagValue ? "flag-output" : ""}>{responseBody}</pre></> : <p>No readable response body was persisted.</p>}
    </section>
    {(requestHeaders || responseHeaders) ? <details className="artifact-technical"><summary>Headers & technical metadata</summary><pre>{JSON.stringify({ request_headers: requestHeaders, response_headers: responseHeaders }, null, 2)}</pre></details> : null}
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

function projectSnapshotAtCursor(snapshot: RuntimeSnapshot, events: RuntimeEvent[], atEnd: boolean): RuntimeSnapshot {
  if (atEnd) return snapshot;

  const solverIds = new Set<string>();
  const solverStatuses = new Map<string, string>();
  const actionIds = new Set<string>();
  const actionStatuses = new Map<string, RuntimeAction["status"]>();
  const artifactIds = new Set<string>();
  const hypothesisIds = new Set<string>();
  const memoryIds = new Set<string>();
  const flagValues = new Set<string>();
  const flagArtifactIds = new Set<string>();
  const findingIds = new Set<string>();
  let sessionStatus: RuntimeSnapshot["session"]["status"] = "created";
  let stopReason: string | null | undefined;
  let activeSolverId: string | null | undefined;
  let turnCount = 0;

  for (const event of events) {
    const solverId = event.solver_id || undefined;
    if (solverId) {
      solverIds.add(solverId);
      activeSolverId = solverId;
      if (!solverStatuses.has(solverId)) solverStatuses.set(solverId, "waiting");
    }

    if (event.type === "SESSION_STARTED") sessionStatus = "running";
    if (event.type === "SESSION_CONTROLLED") {
      if (event.payload.action === "pause") sessionStatus = "paused";
      if (event.payload.action === "resume") sessionStatus = "running";
      if (event.payload.action === "cancel") sessionStatus = "cancelled";
    }
    if (event.type === "SESSION_STOPPED") {
      sessionStatus = sessionStatusValue(event.payload.status) ?? "completed";
      stopReason = event.payload.reason;
      activeSolverId = null;
    }
    if (event.type === "MESSAGE_START") turnCount += 1;
    if (event.type === "SOLVER_STARTED" && solverId) solverStatuses.set(solverId, "running");
    if ((event.type === "SOLVER_STOPPED" || event.type === "AGENT_FINISHED") && solverId) solverStatuses.set(solverId, event.payload.status || "completed");
    if (event.type === "AGENT_ERROR" && solverId) solverStatuses.set(solverId, "failed");

    const actionId = event.payload.action_id;
    if (actionId) {
      actionIds.add(actionId);
      const status = actionStatusAtEvent(event);
      if (status) actionStatuses.set(actionId, status);
    }

    if (event.payload.artifact_id) artifactIds.add(event.payload.artifact_id);
    if (event.payload.evidence_artifact_id) artifactIds.add(event.payload.evidence_artifact_id);
    event.payload.artifact_ids?.forEach((id) => artifactIds.add(id));
    event.payload.artifacts?.forEach((artifact) => artifactIds.add(artifact.artifact_id));

    if (event.payload.hypothesis_id) hypothesisIds.add(event.payload.hypothesis_id);
    if (event.payload.memory_id) memoryIds.add(event.payload.memory_id);
    event.payload.board?.hypotheses.forEach((item) => hypothesisIds.add(item.id));
    event.payload.board?.memory.forEach((item) => memoryIds.add(item.id));

    if (event.type === "FLAG_FOUND" || event.type === "FLAG_CONFIRMED") {
      if (event.payload.value) flagValues.add(event.payload.value);
      const evidenceId = event.payload.artifact_id || event.payload.evidence_artifact_id;
      if (evidenceId) flagArtifactIds.add(evidenceId);
    }
    if (event.type === "FINDING_CONFIRMED" && event.payload.finding_id) findingIds.add(event.payload.finding_id);
  }

  const actions = snapshot.actions.filter((item) => actionIds.has(item.id)).map((item) => {
    const status = actionStatuses.get(item.id) ?? "proposed";
    const finished = ["succeeded", "failed", "blocked", "cancelled"].includes(status);
    return {
      ...item,
      status,
      summary: finished ? item.summary : "",
      error: finished ? item.error : null,
      artifact_ids: item.artifact_ids.filter((id) => artifactIds.has(id)),
    };
  });
  const memory = snapshot.board.memory.filter((item) => memoryIds.has(item.id));
  const hypotheses = snapshot.board.hypotheses.filter((item) => hypothesisIds.has(item.id));
  memory.forEach((item) => item.artifact_ids.forEach((id) => artifactIds.add(id)));
  hypotheses.forEach((item) => item.evidence_artifact_ids.forEach((id) => artifactIds.add(id)));
  const flags = snapshot.flags.filter((item) => flagValues.has(item.value) || flagArtifactIds.has(item.evidence_artifact_id));
  flags.forEach((item) => artifactIds.add(item.evidence_artifact_id));

  return {
    ...snapshot,
    latest_seq: events[events.length - 1]?.seq ?? 0,
    session: { ...snapshot.session, status: sessionStatus, turn_count: turnCount, active_solver_id: activeSolverId, stop_reason: stopReason },
    solvers: snapshot.solvers.filter((item) => solverIds.has(item.id)).map((item) => ({ ...item, status: solverStatuses.get(item.id) ?? "waiting" })),
    subagents: snapshot.subagents.filter((item) => solverIds.has(item.solver_id)),
    challenge: flags.length ? { ...snapshot.challenge, status: "solved", completion_proof_artifact_id: flags[0]?.evidence_artifact_id } : { ...snapshot.challenge, status: sessionStatus === "created" ? "unknown" : "active", completion_proof_artifact_id: null },
    board: { hypotheses, memory },
    actions,
    artifacts: snapshot.artifacts.filter((item) => artifactIds.has(item.id)),
    flags,
    findings: snapshot.findings.filter((item) => findingIds.has(item.id)),
    events,
  };
}

function sessionStatusValue(value?: string): RuntimeSnapshot["session"]["status"] | undefined {
  return (["created", "running", "paused", "blocked", "completed", "failed", "cancelled"] as const).find((item) => item === value);
}

function actionStatusAtEvent(event: RuntimeEvent): RuntimeAction["status"] | undefined {
  if (event.type === "ACTION_PROPOSED") return "proposed";
  if (event.type === "ACTION_APPROVED") return "approved";
  if (event.type === "ACTION_STARTED" || event.type === "TOOL_EXECUTION_START") return "running";
  if (event.type === "RESULT_REJECTED") return "failed";
  if (event.type === "ACTION_FINISHED" || event.type === "TOOL_EXECUTION_END") {
    const value = event.payload.status;
    if (value && ["proposed", "approved", "running", "succeeded", "failed", "blocked", "cancelled"].includes(value)) return value as RuntimeAction["status"];
    return "succeeded";
  }
  return undefined;
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
    nodes.push(node(`mem:${item.id}`, "memory", 690, index * 105, item.content, `${item.kind} · ${sourceLabel(item.source)}`, memoryDetail(item), item.kind === "failure_boundary" ? "failed" : item.kind === "evidence" ? "success" : "neutral", "flow-memory", artifactId));
    const parent = hypotheses.find((hypothesis) => artifactId && hypothesis.evidence_artifact_ids.includes(artifactId));
    edges.push(edge(`memory:${item.id}`, parent ? `hyp:${parent.id}` : "task", `mem:${item.id}`, item.kind === "failure_boundary" ? "failed" : "neutral"));
  });
  return { nodes, edges };
}

function buildTopologyGraph(snapshot: RuntimeSnapshot, events: RuntimeEvent[]): { nodes: FlowNode[]; edges: Edge[] } {
  const actionsPerRow = 2;
  let laneOffset = 0;
  const lanes = snapshot.solvers.map((solver, index) => {
    const actions = snapshot.actions.filter((item) => item.solver_id === solver.id || (!item.solver_id && index === 0));
    const height = Math.max(115, Math.ceil(Math.max(actions.length, 1) / actionsPerRow) * 118);
    const lane = { solver, actions, y: laneOffset, height };
    laneOffset += height + 36;
    return lane;
  });
  const totalHeight = Math.max(115, laneOffset - 36);
  const nodes: FlowNode[] = [node("manager", "manager", 20, (totalHeight - 80) / 2, "Agent Session", `${snapshot.session.status} · turn ${snapshot.session.turn_count}/${snapshot.session.max_turns}`, snapshot.session.stop_reason || "Hosts the persistent model transcript and lifecycle controls.", "manager")];
  const edges: Edge[] = [];
  lanes.forEach(({ solver, actions, y, height }) => {
    const solverY = y + (height - 80) / 2;
    nodes.push(node(`solver:${solver.id}`, "solver", 350, solverY, `${roleLabels[solver.role] ?? solver.role} solver`, `${solver.status} · ${solver.model_name || "model"}`, solver.id, solver.status === "running" ? "running" : "neutral"));
    edges.push(edge(`manager:${solver.id}`, "manager", `solver:${solver.id}`, solver.status === "running" ? "running" : "neutral", true));
    if (actions.length) {
      actions.forEach((action, actionIndex) => {
        const row = Math.floor(actionIndex / actionsPerRow);
        const positionInRow = actionIndex % actionsPerRow;
        const column = row % 2 === 0 ? positionInRow : actionsPerRow - 1 - positionInRow;
        const actionId = `action:${action.id}`;
        const confirmedFlag = snapshot.flags.find((flag) => action.artifact_ids.includes(flag.evidence_artifact_id));
        const tone = confirmedFlag ? "flag" : toneForAction(action);
        nodes.push(node(actionId, "action", 690 + column * 330, y + row * 118, `${actionIndex + 1}. ${actionRequestLabel(action)}`, actionCardMeta(action), actionDetail(action), tone, "flow-action", action.artifact_ids[0], action.id, confirmedFlag?.value, requestPayload(action)));
        const previousId = actionIndex ? `action:${actions[actionIndex - 1]!.id}` : `solver:${solver.id}`;
        edges.push(edge(`tool-chain:${action.id}`, previousId, actionId, tone, true));
      });
    } else {
      const latest = [...events].reverse().find((event) => event.solver_id === solver.id);
      if (latest) nodes.find((item) => item.id === `solver:${solver.id}`)!.data.detail = `${eventTitle(latest.type)}\n${eventSummary(latest, snapshot)}`;
    }
  });
  if (!snapshot.solvers.length) nodes.push(node("waiting", "solver", 350, 0, "Waiting for solver", "not started", "The Agent Session has not started its Solver yet.", "neutral"));
  return { nodes, edges };
}

function node(id: string, kind: FlowKind, x: number, y: number, title: string, meta: string, detail: string, tone: string, testId?: string, artifactId?: string, actionId?: string, flagValue?: string, requestPayloadValue?: string): FlowNode {
  return {
    id, type: "default", position: { x, y }, sourcePosition: Position.Right, targetPosition: Position.Left,
    initialWidth: 280, initialHeight: 80,
    className: `runtime-flow-node ${kind} ${tone}`,
    style: { width: 280 },
    data: { kind, title, meta, detail, artifactId, actionId, flagValue, requestPayload: requestPayloadValue, label: <div aria-label={`${title}. ${detail}`} data-testid={testId ?? (kind === "challenge" ? "flow-challenge" : kind === "artifact" ? "flow-artifact" : undefined)}><span className="flow-node-kicker">{kindLabel(kind)}</span>{flagValue ? <span className="flow-flag-badge" data-testid="flow-action-flag">🏆 FLAG FOUND</span> : null}<b title={title}>{clip(title, 74)}</b><small>{meta}</small></div> },
  };
}

function edge(id: string, source: string, target: string, tone: string, process = false): Edge {
  return { id, source, target, type: "smoothstep", className: `runtime-flow-edge ${tone} ${process ? "process" : ""}`, markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 }, animated: process || tone === "running" };
}

function eventInspector(event: RuntimeEvent, snapshot: RuntimeSnapshot): Inspector {
  const action = event.payload.action_id ? snapshot.actions.find((item) => item.id === event.payload.action_id) : undefined;
  const artifactId = event.payload.evidence_artifact_id || event.payload.artifact_ids?.[0] || action?.artifact_ids[0];
  const confirmedFlag = snapshot.flags.find((flag) => flag.evidence_artifact_id === artifactId);
  return { title: eventTitle(event.type), eyebrow: `Event #${event.seq}`, meta: `${solverLabel(snapshot, event.solver_id)} · ${event.created_at}`, detail: eventSummary(event, snapshot), artifactId, actionId: action?.id, flagValue: confirmedFlag?.value || (event.type === "FLAG_FOUND" ? event.payload.value : undefined), requestPayload: action ? requestPayload(action) : undefined };
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
function actionDetail(item: RuntimeAction) {
  const payload = requestPayload(item);
  return [
    `REQUEST\n${requestMethod(item)} ${item.target}`,
    payload ? `PAYLOAD\n${payload}` : "",
    item.summary ? `OUTCOME\n${item.summary}` : `OUTCOME\n${item.status}`,
    item.rationale ? `WHY THIS STEP\n${item.rationale}` : "",
    item.error?.message ? `ERROR\n${item.error.message}` : "",
  ].filter(Boolean).join("\n\n");
}
function isMcpAction(item: RuntimeAction) { return item.capability === "tool.invoke" || item.capability === "tga_tool_invoke"; }
function toolCallTitle(item: RuntimeAction) {
  if (!isMcpAction(item)) return item.capability;
  const server = String(item.arguments?.tool_id || item.arguments?.tool || "MCP");
  const method = String(item.arguments?.tool_method || item.arguments?.mcp_tool || item.arguments?.tool_name || "tool");
  return `${server}:${method}`;
}
function requestMethod(item: RuntimeAction) {
  return String(item.arguments?.method || (item.capability === "http.request" ? "GET" : toolCallTitle(item))).toUpperCase();
}
function requestPayload(item: RuntimeAction) {
  const value = item.arguments?.body ?? item.arguments?.data ?? item.arguments?.payload;
  return typeof value === "string" ? formatRequestPayload(value) : value == null ? "" : String(value);
}
function actionRequestLabel(item: RuntimeAction) {
  if (isMcpAction(item)) return toolCallTitle(item);
  const method = requestMethod(item);
  const payload = requestPayload(item);
  if (payload) return `${method} ${clip(payload, 52)}`;
  try {
    const target = new URL(item.target);
    return `${method} ${target.pathname}${target.search}`;
  } catch {
    return `${method} ${clip(item.target, 45)}`;
  }
}
function actionCardMeta(item: RuntimeAction) {
  const result = item.summary?.match(/HTTP\s+(\d{3}).*?\((\d+)\s*ms\)/i);
  const parts = result ? [`HTTP ${result[1]}`, `${result[2]} ms`] : [item.status];
  parts.push(`${item.artifact_ids.length} artifact${item.artifact_ids.length === 1 ? "" : "s"}`);
  return parts.join(" · ");
}
function toolCallSummary(snapshot: RuntimeSnapshot) {
  const mcp = snapshot.actions.filter(isMcpAction).length;
  const native = snapshot.actions.length - mcp;
  return `${snapshot.solvers.length} solver · ${native} native · ${mcp} MCP`;
}
function formatRequestPayload(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "";
  try {
    const json = JSON.parse(trimmed) as unknown;
    return JSON.stringify(json, null, 2);
  } catch {
    // Form-encoded command bodies are the common case for the native HTTP tool.
  }
  if (trimmed.includes("=")) {
    try {
      const params = new URLSearchParams(trimmed);
      const entries = [...params.entries()];
      if (entries.length) return entries.map(([key, entry]) => `${key} = ${entry}`).join("\n");
    } catch {
      // Preserve the original payload when it is not valid form encoding.
    }
  }
  try { return decodeURIComponent(trimmed.replace(/\+/g, " ")); } catch { return trimmed; }
}
function parseArtifactPreview(preview?: string): Record<string, unknown> | null {
  if (!preview) return null;
  try {
    const parsed = JSON.parse(preview) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : { value: parsed };
  } catch {
    return { body_text: preview };
  }
}
function textValue(value: unknown) { return value == null ? "" : typeof value === "string" ? value : String(value); }
function recordValue(value: unknown) { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null; }
function readableResponseBody(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "";
  if (!/<[a-z][\s\S]*>/i.test(trimmed)) {
    try { return JSON.stringify(JSON.parse(trimmed), null, 2); } catch { return trimmed; }
  }
  const normalizedHtml = trimmed
    // PHP/source snippets inside an HTML response are evidence, not processing
    // instructions. Protect their opening token before the browser parses HTML.
    .replace(/<\?(?=[a-z])/gi, "&lt;?")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(?:div|p|li|h[1-6]|tr|section|article|main|pre)>/gi, "\n$&");
  const container = document.createElement("div");
  container.innerHTML = normalizedHtml;
  container.querySelectorAll("style,script,svg,noscript").forEach((node) => node.remove());
  const focused = container.querySelector(".result, .output, [class*='result'], pre, main");
  const focusedText = normalizeEvidenceText(focused?.textContent || "");
  const allText = normalizeEvidenceText(container.textContent || "");
  return focusedText || allText;
}
function normalizeEvidenceText(value: string) {
  return value.replace(/\r\n?/g, "\n").split("\n").map((line) => line.replace(/[ \t]+/g, " ").trim()).join("\n").replace(/\n{3,}/g, "\n\n").trim();
}
function sourceLabel(value: string) { return value.replace(/^solver:/, "").slice(0, 22); }
function toneForHypothesis(item: Hypothesis) { return item.status === "verified" ? "success" : item.status === "rejected" || item.status === "superseded" ? "failed" : item.status === "testing" ? "running" : "neutral"; }
function toneForAction(item: RuntimeAction) { return item.status === "succeeded" ? "success" : ["failed", "blocked", "cancelled"].includes(item.status) ? "failed" : item.status === "running" ? "running" : "neutral"; }
function kindLabel(kind: FlowKind) { return ({ challenge: "TARGET", hypothesis: "LEGACY IDEA", memory: "CONTEXT", manager: "SESSION", solver: "SOLVER", action: "TOOL CALL", artifact: "ARTIFACT" } as const)[kind]; }
function clip(value: string, limit: number) { const text = value.replace(/\s+/g, " ").trim(); return text.length > limit ? `${text.slice(0, limit)}…` : text; }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
