import { BrainCircuit, Check, ChevronDown, ChevronUp, Clock3, Database, FileText, Files, Library, Minus, Play, Terminal, X } from "lucide-react";
import { useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { TGA3Agent, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { agentNode, compact, currentAgentAction, latestAgentEntry, modelNode, runtimeActivityEvents, type RuntimeActivity } from "../runtime-activity";
import { activityRoute, labelPlacement, phaseRoute, type GraphBox, type GraphPoint } from "../runtime-geometry";
import { formatTime, sdkLabel, stateLabel } from "../tga3-view";
import { useRuntimeActivities } from "../use-runtime-activities";

const ORDER = ["scout", "worker-openai", "worker-claude", "reporter"];
export function TGA3RuntimeGraph({ snapshot, selectedAgentId, onSelectAgent }: { snapshot: TGA3RuntimeSnapshot; selectedAgentId?: string | null; onSelectAgent?: (id: string) => void }) {
  const [collapsed, setCollapsed] = useState(false), [models, setModels] = useState(false), [selected, setSelected] = useState<string | null>(null);
  const stage = useRef<HTMLDivElement>(null), marker = useId().replace(/:/g, "");
  const [boxes, setBoxes] = useState<Record<string, GraphBox>>({}), [size, setSize] = useState({ width: 1, height: 1 });
  const activities = useRuntimeActivities(snapshot), events = useMemo(() => runtimeActivityEvents(snapshot), [snapshot]);
  const active = new Set(activities.flatMap(item => [item.from, ...(item.to ? [item.to] : [])]));
  const terminal = ["completed", "failed", "stopped", "cancelled"].includes(snapshot.task.state);
  const counts = { intel: snapshot.blackboard.filter(e => e.kind === "intel").length, finding: snapshot.blackboard.filter(e => e.kind === "finding").length, final: snapshot.blackboard.filter(e => e.kind === "final_candidate").length };
  const agents = [...snapshot.agents].sort((a, b) => ORDER.indexOf(a.agent_id) - ORDER.indexOf(b.agent_id));
  const detail = selected ? [...events].reverse().find(event => event.activity && [event.activity.from, event.activity.to].includes(selected))?.activity : null;
  const selectedAgent = agents.find(agent => agentNode(agent.agent_id) === selected);
  useLayoutEffect(() => {
    const canvas = stage.current; if (!canvas || collapsed) return;
    const measure = () => {
      const root = canvas.getBoundingClientRect(), next: Record<string, GraphBox> = {};
      canvas.querySelectorAll<HTMLElement>("[data-graph-node]").forEach(node => { const r = node.getBoundingClientRect(); next[node.dataset.graphNode!] = { x: r.left - root.left, y: r.top - root.top, width: r.width, height: r.height }; });
      setBoxes(next); setSize({ width: canvas.clientWidth, height: canvas.clientHeight });
    };
    measure(); const observer = new ResizeObserver(measure); observer.observe(canvas); canvas.querySelectorAll<HTMLElement>("[data-graph-node]").forEach(node => observer.observe(node)); return () => observer.disconnect();
  }, [collapsed, models, snapshot, selectedAgentId]);
  function selectNode(id: string) { setSelected(current => current === id ? null : id); if (id.startsWith("agent:")) onSelectAgent?.(id.slice(6)); }
  function resource(id: string, title: string, icon: ReactNode, description: ReactNode) {
    return <button type="button" className={`collab-node collab-resource resource-${id}`} data-graph-node={id} data-active={active.has(id)} aria-pressed={selected === id} onClick={() => selectNode(id)}><span className="collab-node-title">{icon}<strong>{title}</strong></span><small>{description}</small></button>;
  }
  function agent(id: string) {
    const value = agents.find(item => item.agent_id === id);
    if (!value) return <div className="collab-node collab-placeholder"><strong>{id === "scout" ? "Scout" : id === "reporter" ? "Reporter" : id}</strong><small>尚未建立会话</small></div>;
    const node = agentNode(id), action = currentAgentAction(snapshot, value), entry = latestAgentEntry(snapshot.blackboard, id);
    return <button type="button" className="collab-node collab-agent" data-graph-node={node} data-active={active.has(node)} aria-label={`查看 ${value.display_name}`} aria-pressed={selectedAgentId === id} onClick={() => selectNode(node)}>
      <span className="collab-node-title"><strong>{value.display_name}</strong><Terminal size={14} /></span>
      <span className="collab-agent-state">{value.actual_state === "completed" ? <Check size={11} /> : ["running", "starting"].includes(value.actual_state) ? <Play size={11} /> : <Minus size={11} />}{stateLabel(value.actual_state)}</span>
      <span className="collab-agent-action" title={action?.detail}>{action ? <><Terminal size={12} /><span>{action.pending ? "" : "最近："}{action.text}</span></> : <><Clock3 size={12} /><span>{idleAction(value, entry?.seq)}</span></>}</span>
      {models ? <span className="collab-model" data-graph-node={modelNode(id)}><BrainCircuit size={12} /><span>{value.model_name ?? value.model_id}<small>{sdkLabel(value.sdk)}</small></span></span> : null}
    </button>;
  }
  const labels: Array<{ key: string; activity: RuntimeActivity; point: GraphPoint }> = [];
  for (const activity of activities) { const route = activityRoute(activity, boxes); if (!route) continue; const point = labelPlacement(route.label, activity.shortLabel, boxes, size, labels.map(item => ({ point: item.point, text: item.activity.shortLabel }))); if (point) labels.push({ key: activity.id, activity, point }); }
  return <section className="collab-graph" aria-label="实时运行图">
    <header className="collab-head"><div><h2>实时运行图</h2><small>{terminal ? "运行已结束" : "实时交互"}</small></div><div><label><input type="checkbox" checked={models} onChange={event => setModels(event.target.checked)} />模型信息</label><button type="button" aria-expanded={!collapsed} onClick={() => setCollapsed(value => !value)}>{collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}{collapsed ? "展开运行图" : "收起运行图"}</button></div></header>
    {!collapsed ? <><div className="collab-stage" ref={stage}>
      <svg className="collab-wires" viewBox={`0 0 ${size.width} ${size.height}`} aria-hidden="true"><defs><marker id={marker} viewBox="0 0 8 8" markerWidth="6" markerHeight="6" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="none" stroke="currentColor" /></marker></defs>
        {size.width >= 530 ? [["scout", "worker-openai"], ["scout", "worker-claude"], ["worker-openai", "reporter"], ["worker-claude", "reporter"]].map(([a, b]) => boxes[agentNode(a)] && boxes[agentNode(b)] ? <path key={`${a}-${b}`} className="collab-passive" d={phaseRoute(boxes[agentNode(a)], boxes[agentNode(b)])} /> : null) : null}
        {activities.map(activity => { const route = activityRoute(activity, boxes); return route ? <path key={activity.id} className="collab-route" markerEnd={`url(#${marker})`} d={route.d} /> : null; })}
      </svg>
      <div className="collab-grid">
        {resource("input", "任务输入", <Files size={15} />, `题目描述与 ${snapshot.blackboard.filter(item => item.kind === "user_file").length} 个附件`)}
        {resource("skills", "Skills", <Library size={15} />, "按需读取能力包与补充材料")}
        {resource("output", "报告", <FileText size={15} />, snapshot.task.state === "completed" ? "Markdown Writeup 已生成" : snapshot.task.state === "reporting" ? "正在整理步骤与证据" : "等待 Reporter 生成")}
        <div className="collab-scout"><span className="collab-phase">侦察</span>{agent("scout")}</div><div className="collab-workers"><span className="collab-phase">并行执行</span>{agent("worker-openai")}{agent("worker-claude")}</div><div className="collab-reporter"><span className="collab-phase">报告</span>{agent("reporter")}</div>
        <small className="collab-input-note">原始材料共享只读</small>
        <button type="button" className="collab-node collab-blackboard" data-graph-node="blackboard" data-active={active.has("blackboard")} aria-pressed={selected === "blackboard"} onClick={() => selectNode("blackboard")}><span className="collab-node-title"><Database size={15} /><strong>黑板</strong><em>#{snapshot.task.blackboard_seq}</em></span><span className="collab-board-counts">{counts.intel} Intel <i>/</i> {counts.finding} Findings <i>/</i> {counts.final} 最终候选</span><small>各 Worker 独立记录已读位置</small></button><small className="collab-output-note">Markdown Writeup</small>
      </div>
      <div className="collab-labels" aria-hidden="true">{labels.map(({ key, activity, point }) => <span key={key} style={{ left: point.x, top: point.y }}>{activity.shortLabel}</span>)}</div>
    </div><div className="collab-now" aria-live="polite">{activities.length ? activities.map(activity => <div key={activity.id} className={activity.to ? "interaction" : "action"}><Terminal size={13} /><span>{activity.label}<small>{activity.to ? `${nodeName(activity.from, agents)} → ${nodeName(activity.to, agents)}` : compact(activity.detail, 90)}</small></span></div>) : <span className="collab-idle">{terminal ? "本次运行交互已完成" : "等待新的动作与交互；节点保留最近执行状态"}</span>}</div>
    {selected ? <div className="collab-detail"><div><h3>{nodeName(selected, agents)}</h3><small>{detail ? formatTime(new Date(detail.at).toISOString()) : "节点详情"}</small></div><div>{detail ? <><p>{detail.label}</p><pre>{detail.detail}</pre></> : selectedAgent ? <><p>{sdkLabel(selectedAgent.sdk)} / {selectedAgent.model_name ?? selectedAgent.model_id}</p><p>{stateLabel(selectedAgent.actual_state)}{selectedAgent.metadata?.blackboard_cursor !== undefined ? ` · 黑板已读至 #${selectedAgent.metadata.blackboard_cursor}` : ""}</p></> : <p>{resourceDetail(selected, snapshot)}</p>}</div><button type="button" aria-label="关闭节点详情" onClick={() => setSelected(null)}><X size={14} /></button></div> : null}</> : null}
    <footer className="collab-foot"><span><i />当前交互 <i className="passive" />阶段与关联</span><small>点击节点查看动作详情</small></footer>
  </section>;
}
function idleAction(agent: TGA3Agent, seq?: number) { if (agent.actual_state === "failed") return "执行失败，查看最近错误"; if (["paused", "pause_requested"].includes(agent.actual_state)) return "保留原 CLI 会话"; if (seq) return `最近写入黑板 #${seq}`; if (["completed", "stopped"].includes(agent.actual_state)) return "本阶段执行结束"; return agent.role === "reporter" ? "等待最终候选" : "等待新的执行事件"; }
function nodeName(id: string, agents: TGA3Agent[]) { if (id.startsWith("agent:") || id.startsWith("model:")) { const a = agents.find(item => item.agent_id === id.slice(6)); return id.startsWith("model:") ? a?.model_name ?? a?.model_id ?? "模型" : a?.display_name ?? id.slice(6); } return ({ input: "任务输入", blackboard: "黑板", skills: "Skills", output: "报告" } as Record<string, string>)[id] ?? id; }
function resourceDetail(id: string, snapshot: TGA3RuntimeSnapshot) { return id === "blackboard" ? `共享事实已写入至 #${snapshot.task.blackboard_seq}。Scout 发布 Intel，Workers 发布 Findings 与最终候选，Reporter 读取最终快照。` : id === "skills" ? "Agent 根据需要读取 /skills 下的能力包。未发生读取时不显示交互。" : id === "input" ? "题目描述和附件索引进入黑板，原始文件只读提供给各 Agent。" : "Reporter 根据最终黑板与只读证据生成 Markdown Writeup。"; }
