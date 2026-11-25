import {
  BookOpen,
  Database,
  FileText,
  Files,
  GitFork,
  Library,
  RotateCcw,
  Terminal,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { TGA3Agent, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { agentNode, compact, currentAgentAction, latestAgentEntry, runtimeActivityEvents } from "../runtime-activity";
import { agentStateLabel, formatTime, sdkLabel, stateTone } from "../tga3-view";
import { useRuntimeActivities } from "../use-runtime-activities";
import { ExperienceCards } from "./ExperienceCards";

const ORDER = ["scout", "worker-openai", "worker-claude", "worker-opencode", "worker-qwen", "reporter"];

function shortSdkCode(sdk: string): string {
  return (
    ({
      codex_cli: "CDX",
      claude_cli: "CLD",
      opencode_cli: "OCD",
      qwen_cli: "QWN",
    } as Record<string, string>)[sdk] ?? sdkLabel(sdk)
  );
}

export function TGA3RuntimeGraph({
  snapshot,
  selectedAgentId,
  topologyOpen,
  onToggleTopology,
  onSelectAgent,
}: {
  snapshot: TGA3RuntimeSnapshot;
  selectedAgentId?: string | null;
  topologyOpen?: boolean;
  onToggleTopology?: (open: boolean) => void;
  onSelectAgent?: (id: string) => void;
}) {
  const [internalOpen, setInternalOpen] = useState(true);
  const isOpen = topologyOpen !== undefined ? topologyOpen : internalOpen;
  const setOpen = (next: boolean) => {
    if (onToggleTopology) onToggleTopology(next);
    else setInternalOpen(next);
  };

  const [models, setModels] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const activities = useRuntimeActivities(snapshot);
  const events = useMemo(() => runtimeActivityEvents(snapshot), [snapshot]);
  const activeSet = new Set(activities.flatMap((item) => [item.from, ...(item.to ? [item.to] : [])]));
  const terminal = ["completed", "failed", "stopped", "cancelled"].includes(snapshot.task.state);
  const questions = snapshot.aoe?.questions;
  const counts = {
    intel: snapshot.blackboard.filter((e) => e.kind === "intel").length,
    finding: snapshot.blackboard.filter((e) => e.kind === "finding").length,
    final: snapshot.blackboard.filter((e) => e.kind === "final_candidate").length,
  };

  const agents = [...snapshot.agents].sort((a, b) => {
    const ai = ORDER.indexOf(a.agent_id);
    const bi = ORDER.indexOf(b.agent_id);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
  const scout = agents.find((a) => a.role === "scout");
  const reporter = agents.find((a) => a.role === "reporter");
  const workers = agents.filter((a) => a.role === "worker");

  const detail = selectedNode
    ? [...events].reverse().find((ev) => ev.activity && [ev.activity.from, ev.activity.to].includes(selectedNode))?.activity
    : null;
  const selectedAgent = agents.find((a) => agentNode(a.agent_id) === selectedNode);

  function handleNodeClick(nodeId: string) {
    setSelectedNode((curr) => (curr === nodeId ? null : nodeId));
    if (nodeId.startsWith("agent:")) {
      onSelectAgent?.(nodeId.slice(6));
    }
  }

  function agentActionText(agent: TGA3Agent) {
    const currentQuestion = agent.metadata?.current_question_id;
    const scoped =
      questions && currentQuestion
        ? { ...snapshot, events: snapshot.events.filter((ev) => (ev.question_id ?? ev.payload.question_id) === currentQuestion) }
        : snapshot;
    const action = currentAgentAction(scoped, agent);
    const entry = latestAgentEntry(snapshot.blackboard, agent.agent_id);
    if (action) return `${action.pending ? "" : "最近："}${action.text}`;
    if (agent.actual_state === "failed") return "执行失败，查看最近错误";
    if (["paused", "pause_requested"].includes(agent.actual_state)) return "保留原 CLI 会话";
    if (entry?.seq) return `最近写入黑板 #${entry.seq}`;
    if (["completed", "stopped"].includes(agent.actual_state)) return "本阶段执行结束";
    return agent.role === "reporter" ? "等待最终候选" : "等待新的执行事件";
  }

  const workerTopPositions =
    workers.length <= 2 ? [115, 295] : workers.length === 3 ? [45, 195, 345] : [16, 140, 264, 388];

  return (
    <section
      className={`runtime-fleet-section${isOpen ? " is-canvas-open" : ""}`}
      aria-label="实时运行图"
      style={isOpen ? { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" } : undefined}
    >
      {/* Row 2: 56px Always-Visible Live Fleet Strip */}
      <div className="fleet-strip-root">
        <div className="fleet-strip-slots" role="group" aria-label="活体舰队槽位">
          {agents.map((item) => {
            const isSel = selectedAgentId === item.agent_id;
            const tone = stateTone(item.actual_state);
            const pct =
              item.actual_state === "completed"
                ? 100
                : ["running", "starting"].includes(item.actual_state)
                  ? 65
                  : item.actual_state === "paused"
                    ? 40
                    : 15;
            return (
              <button
                key={item.agent_id}
                type="button"
                className={`fleet-slot${isSel ? " is-selected" : ""}`}
                aria-label={`聚焦 ${item.display_name}`}
                aria-pressed={isSel}
                onClick={() => onSelectAgent?.(item.agent_id)}
              >
                <span className="fleet-slot-top">
                  <span className="fleet-slot-identity">
                    <i className={`fleet-dot tone-${tone}`} aria-hidden="true" />
                    <strong>{item.display_name}</strong>
                    <span className={`cli-badge is-${item.sdk}`}>{shortSdkCode(item.sdk)}</span>
                  </span>
                  <span className="mono subtle" style={{ fontSize: "10px" }}>
                    {agentStateLabel(item)}
                  </span>
                </span>
                <span className="fleet-slot-meta">
                  <span className="fleet-slot-sub">
                    {item.metadata?.current_question_title ?? agentActionText(item)}
                  </span>
                  <small>
                    {item.metadata?.token_usage?.reported
                      ? `${(item.metadata.token_usage.total_tokens / 1000).toFixed(1)}k`
                      : ""}
                  </small>
                </span>
                <div className="proto-progress">
                  <div
                    className={`proto-progress-fill${tone === "success" ? " is-ok" : tone === "waiting" ? " is-warn" : ""}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </button>
            );
          })}
        </div>

        <button
          type="button"
          className={`fleet-topo-toggle${isOpen ? " is-active" : ""}`}
          aria-expanded={isOpen}
          onClick={() => setOpen(!isOpen)}
        >
          <GitFork size={14} />
          <span>{isOpen ? "收起运行图" : "展开运行图"}</span>
        </button>
      </div>

      {/* Row 3B: Full-Height Prototype Spatial Swarm Topology Canvas */}
      {isOpen ? (
        <div className="spatial-canvas-wrap">
          <div className="spatial-toolbar">
            <span className="proto-section-title">
              <i className={terminal ? "idle-dot" : "running-dot"} /> SPATIAL SWARM TOPOLOGY
            </span>
            <span className="tele-divider" />
            <label className="mono muted" style={{ fontSize: "11px", display: "inline-flex", alignItems: "center", gap: "5px", cursor: "pointer" }}>
              <input type="checkbox" checked={models} onChange={(e) => setModels(e.target.checked)} />
              模型信息
            </label>
            <span className="tele-divider" />
            <button type="button" className="ref-secondary-button" style={{ height: 22, padding: "0 6px" }} aria-label="缩小" onClick={() => setZoom((z) => Math.max(0.7, +(z - 0.1).toFixed(2)))}>
              <ZoomOut size={12} />
            </button>
            <span className="mono" style={{ fontSize: "11px", minWidth: 36, textAlign: "center" }}>
              {Math.round(zoom * 100)}%
            </span>
            <button type="button" className="ref-secondary-button" style={{ height: 22, padding: "0 6px" }} aria-label="放大" onClick={() => setZoom((z) => Math.min(1.35, +(z + 0.1).toFixed(2)))}>
              <ZoomIn size={12} />
            </button>
            <button type="button" className="ref-secondary-button" style={{ height: 22, padding: "0 7px" }} onClick={() => setZoom(1)}>
              <RotateCcw size={11} /> 重置
            </button>
            {onToggleTopology ? (
              <>
                <span className="tele-divider" />
                <button type="button" className="ref-primary-button" style={{ height: 24 }} onClick={() => setOpen(false)}>
                  返回分屏作战室
                </button>
              </>
            ) : null}
          </div>

          <div className="spatial-stage-scroll">
            <div className="spatial-stage" style={{ transform: `scale(${zoom})` }}>
              <svg className="spatial-svg" viewBox="0 0 1080 520" aria-hidden="true">
                {/* Input -> Scout & Blackboard */}
                <path className={`spatial-edge${activeSet.has("input") ? " is-active" : ""}`} d="M 244 76 C 310 76, 330 225, 412 225" />
                {/* Scout -> Blackboard */}
                <path
                  className={`spatial-edge${!terminal && (activeSet.has("agent:scout") || scout?.actual_state === "running") ? " is-active" : ""}`}
                  d="M 244 198 C 320 198, 340 236, 412 236"
                />
                {/* Skills -> Blackboard */}
                <path className={`spatial-edge${activeSet.has("skills") ? " is-active" : ""}`} d="M 244 328 C 320 328, 340 258, 412 258" />
                {/* Knowledge -> Blackboard */}
                <path className={`spatial-edge${activeSet.has("knowledge") ? " is-active" : ""}`} d="M 244 438 C 320 438, 340 272, 412 272" />

                {/* Blackboard -> Each Real Worker */}
                {workers.map((w, idx) => {
                  const wy = (workerTopPositions[idx] ?? 16 + idx * 124) + 52;
                  const active = !terminal && (activeSet.has(agentNode(w.agent_id)) || ["running", "starting"].includes(w.actual_state));
                  return (
                    <path
                      key={w.agent_id}
                      className={`spatial-edge${active ? " is-active" : ""}`}
                      d={`M 648 ${232 + idx * 12} C 720 ${232 + idx * 12}, 740 ${wy}, 820 ${wy}`}
                    />
                  );
                })}

                {/* Blackboard -> Experience & Reporter */}
                <path className={`spatial-edge${activeSet.has("experience") || activeSet.has("agent:reporter") ? " is-active" : ""}`} d="M 530 296 L 530 356" />
                <path className={`spatial-edge${activeSet.has("output") ? " is-active" : ""}`} d="M 530 432 L 530 456" />
              </svg>

              {/* Left Column: Input, Scout, Skills, Knowledge */}
              <button
                type="button"
                className={`spatial-node${selectedNode === "input" ? " is-selected" : ""}`}
                data-active={activeSet.has("input")}
                style={{ left: 30, top: 24 }}
                onClick={() => handleNodeClick("input")}
              >
                <span className="spatial-node-head">
                  <strong className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Files size={13} /> 任务输入
                  </strong>
                  <span className="ref-chip">READONLY</span>
                </span>
                <span className="spatial-node-sub">
                  {questions
                    ? `${questions.length} 道题目，各自题面与附件`
                    : `题目描述与 ${snapshot.blackboard.filter((i) => i.kind === "user_file").length} 个附件`}
                </span>
              </button>

              {scout ? (
                <button
                  type="button"
                  className={`spatial-node${selectedAgentId === scout.agent_id || selectedNode === "agent:scout" ? " is-selected" : ""}`}
                  data-active={activeSet.has("agent:scout")}
                  aria-label={`查看 ${scout.display_name}`}
                  style={{ left: 30, top: 132 }}
                  onClick={() => handleNodeClick("agent:scout")}
                >
                  <span className="spatial-node-head">
                    <strong>{scout.display_name}</strong>
                    <span className={`cli-badge is-${scout.sdk}`}>{shortSdkCode(scout.sdk)}</span>
                  </span>
                  <span className="spatial-node-sub">{agentStateLabel(scout)}</span>
                  <div className="spatial-attach">
                    <span className="truncate">{agentActionText(scout)}</span>
                    {models ? <span className="mono subtle">{scout.model_name ?? scout.model_id}</span> : null}
                  </div>
                </button>
              ) : null}

              <button
                type="button"
                className={`spatial-node is-bus${selectedNode === "skills" ? " is-selected" : ""}`}
                data-active={activeSet.has("skills")}
                style={{ left: 30, top: 282, width: 214 }}
                onClick={() => handleNodeClick("skills")}
              >
                <span className="spatial-node-head">
                  <strong className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Library size={13} /> Skills
                  </strong>
                  <span className="ref-chip">MOUNTED</span>
                </span>
                <span className="spatial-node-sub">按需读取能力包与补充材料</span>
              </button>

              <button
                type="button"
                className={`spatial-node is-bus${selectedNode === "knowledge" ? " is-selected" : ""}`}
                data-active={activeSet.has("knowledge")}
                style={{ left: 30, top: 392, width: 214 }}
                onClick={() => handleNodeClick("knowledge")}
              >
                <span className="spatial-node-head">
                  <strong className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <BookOpen size={13} /> CTF 知识库
                  </strong>
                  <span className="ref-chip tone-info">
                    {snapshot.task.metadata?.knowledge_source === "local" ? "LOCAL" : "ctf42"}
                  </span>
                </span>
                <span className="spatial-node-sub">
                  {snapshot.task.metadata?.knowledge_source === "local"
                    ? "CTF Wiki · 本地章节 / 全文检索"
                    : "ctf42 · 在线技法 / 历史题目 / WP"}
                </span>
              </button>

              {/* Center Column: Shared Blackboard + Experience Cards + Reporter / Output */}
              <button
                type="button"
                className={`spatial-node is-bus${selectedNode === "blackboard" ? " is-selected" : ""}`}
                data-active={activeSet.has("blackboard")}
                style={{ left: 412, top: 186 }}
                onClick={() => handleNodeClick("blackboard")}
              >
                <span className="spatial-node-head">
                  <strong className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Database size={13} /> {questions ? "逐题黑板" : "黑板"}
                  </strong>
                  <span className="ref-chip tone-accent">SEQ #{snapshot.task.blackboard_seq}</span>
                </span>
                <span className="spatial-node-sub">
                  {questions
                    ? `${questions.length} 道题 / ${questions.reduce((s, q) => s + q.blackboard_seq, 0)} 条记录`
                    : `${counts.intel} Intel · ${counts.finding} Findings · ${counts.final} 候选`}
                </span>
                <div className="spatial-attach">
                  <span className="mono subtle">游标广播 · 各 Worker 独立记录已读位置</span>
                </div>
              </button>

              <button
                type="button"
                className={`spatial-node is-bus${selectedNode === "experience" ? " is-selected" : ""}`}
                data-active={activeSet.has("experience")}
                style={{ left: 412, top: 348 }}
                onClick={() => handleNodeClick("experience")}
              >
                <span className="spatial-node-head">
                  <strong className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <BookOpen size={13} /> 经验卡
                  </strong>
                  <span className="ref-chip tone-ok">{snapshot.experience_count ?? 0} 张</span>
                </span>
                <span className="spatial-node-sub">点击展开当前任务沉淀的经验卡</span>
              </button>

              <button
                type="button"
                className={`spatial-node${selectedNode === "output" || selectedNode === "agent:reporter" ? " is-selected" : ""}`}
                data-active={activeSet.has("output") || activeSet.has("agent:reporter")}
                aria-label={reporter ? `查看 ${reporter.display_name}` : "查看报告"}
                style={{ left: 412, top: 432, width: 236 }}
                onClick={() => handleNodeClick(reporter ? "agent:reporter" : "output")}
              >
                <span className="spatial-node-head">
                  <strong style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <FileText size={13} /> {reporter ? <>{reporter.display_name} · <span>报告</span></> : "报告"}
                  </strong>
                  {reporter ? <span className={`cli-badge is-${reporter.sdk}`}>{shortSdkCode(reporter.sdk)}</span> : null}
                </span>
                <span className="spatial-node-sub">
                  {snapshot.task.metadata?.produce_report === false
                    ? "未启用报告 · 仅整理经验卡"
                    : questions
                      ? `${questions.filter((q) => q.report_state === "completed").length} / ${questions.length} 份逐题报告`
                      : snapshot.task.state === "completed"
                        ? "Markdown Writeup 已生成"
                        : "等待最终候选生成 Writeup"}
                </span>
                {models && reporter ? <span className="spatial-node-sub">{reporter.model_name ?? reporter.model_id}</span> : null}
              </button>

              {/* Right Column: All Real Workers (REAL display_name as prominent header + decoupled CLI badge!) */}
              {workers.map((worker, idx) => {
                const top = workerTopPositions[idx] ?? 16 + idx * 124;
                const node = agentNode(worker.agent_id);
                const tone = stateTone(worker.actual_state);
                const isSel = selectedAgentId === worker.agent_id || selectedNode === node;
                const tok = worker.metadata?.token_usage?.reported
                  ? `${(worker.metadata.token_usage.total_tokens / 1000).toFixed(1)}k tok`
                  : agentStateLabel(worker);
                return (
                  <button
                    key={worker.agent_id}
                    type="button"
                    className={`spatial-node${isSel ? " is-selected" : ""}`}
                    data-active={activeSet.has(node)}
                    aria-label={`查看 ${worker.display_name}`}
                    style={{ left: 820, top, width: 232 }}
                    onClick={() => handleNodeClick(node)}
                  >
                    <span className="spatial-node-head">
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                        <i className={`fleet-dot tone-${tone}`} />
                        <strong>{worker.display_name}</strong>
                      </span>
                      <span className={`cli-badge is-${worker.sdk}`}>{shortSdkCode(worker.sdk)}</span>
                    </span>
                    <span className="spatial-node-sub">
                      {agentStateLabel(worker)} · {tok}
                    </span>
                    {models ? <span className="spatial-node-sub">{worker.model_name ?? worker.model_id}</span> : null}
                    <div className="spatial-attach">
                      <div className="spatial-attach-top">
                        <strong className="truncate" style={{ fontSize: "11px" }}>
                          {worker.metadata?.current_question_title
                            ? `题目：${worker.metadata.current_question_title}`
                            : agentActionText(worker)}
                        </strong>
                      </div>
                      <div className="proto-progress">
                        <div
                          className={`proto-progress-fill${tone === "success" ? " is-ok" : tone === "waiting" ? " is-warn" : ""}`}
                          style={{ width: worker.actual_state === "completed" ? "100%" : worker.actual_state === "running" ? "64%" : "25%" }}
                        />
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Live Activity Ticker */}
          <div className="spatial-bottom-bar collab-now" aria-live="polite">
            {activities.length ? (
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                {activities.map((activity) => (
                  <span key={activity.id} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Terminal size={12} />
                    <strong>{activity.label}</strong>
                    <small className="muted">
                      {activity.to
                        ? `${nodeName(activity.from, agents)} → ${nodeName(activity.to, agents)}`
                        : compact(activity.detail, 80)}
                    </small>
                  </span>
                ))}
              </div>
            ) : (
              <span className="muted">
                {terminal ? "本次运行交互已完成" : "等待新的动作与交互；点击任意画布节点查看详情或联动右侧 Inspector"}
              </span>
            )}
          </div>

          {selectedNode ? (
            <div className="spatial-detail-drawer">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <strong className="mono">{nodeName(selectedNode, agents)}</strong>
                  <small className="muted" style={{ marginLeft: 8 }}>
                    {detail ? formatTime(new Date(detail.at).toISOString()) : "节点详情"}
                  </small>
                </div>
                <button type="button" className="text-button" aria-label="关闭节点详情" onClick={() => setSelectedNode(null)}>
                  <X size={14} />
                </button>
              </div>
              {selectedNode === "experience" ? (
                <ExperienceCards taskId={snapshot.task.id} />
              ) : detail ? (
                <div>
                  <p style={{ margin: "0 0 6px" }}>{detail.label}</p>
                  <pre className="bb-code" style={{ margin: 0 }}>{detail.detail}</pre>
                </div>
              ) : selectedAgent ? (
                <div className="mono muted" style={{ fontSize: "12px" }}>
                  {sdkLabel(selectedAgent.sdk)} / {selectedAgent.model_name ?? selectedAgent.model_id} ·{" "}
                  {agentStateLabel(selectedAgent)}
                  {selectedAgent.metadata?.blackboard_cursor !== undefined
                    ? ` · 黑板已读至 #${selectedAgent.metadata.blackboard_cursor}`
                    : ""}
                </div>
              ) : (
                <div style={{ fontSize: "12px" }} className="muted">
                  {resourceDetail(selectedNode, snapshot)}
                </div>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function nodeName(id: string, agents: TGA3Agent[]) {
  if (id.startsWith("agent:") || id.startsWith("model:")) {
    const a = agents.find((item) => item.agent_id === id.slice(6));
    return id.startsWith("model:") ? a?.model_name ?? a?.model_id ?? "模型" : a?.display_name ?? id.slice(6);
  }
  return (
    ({
      input: "任务输入",
      blackboard: "黑板",
      skills: "Skills",
      knowledge: "CTF 知识库",
      experience: "经验卡",
      output: "报告",
    } as Record<string, string>)[id] ?? id
  );
}

function resourceDetail(id: string, snapshot: TGA3RuntimeSnapshot) {
  return id === "knowledge" && snapshot.task.metadata?.knowledge_source === "local" ? (
    <>
      各 Worker 通过本地 MCP 检索 CTF Wiki 章节并分页读取正文，无需外部检索请求。资料需在当前题目中验证。
      <a href="https://github.com/ctf-wiki/ctf-wiki" target="_blank" rel="noreferrer" style={{ marginLeft: 6 }}>
        查看 CTF Wiki 来源
      </a>
    </>
  ) : id === "knowledge" ? (
    <>
      各 Worker 通过在线 MCP 检索 ctf42 的技法、技术栈和历史题目；按需读取题目详情及 WP 链接，外部参考需在当前题目中验证。
      <a href="https://ctf42.online/about/" target="_blank" rel="noreferrer" style={{ marginLeft: 6 }}>
        查看知识库与接入说明
      </a>
    </>
  ) : id === "blackboard" && snapshot.aoe?.scout_mode === "collection_only" ? (
    "各题题面与附件分别登记到黑板；Workers 在解题过程中发布 Finding 与最终候选，Reporter 按题读取最终快照。"
  ) : id === "blackboard" ? (
    `共享事实已写入至 #${snapshot.task.blackboard_seq}。系统导入 Scout 的 Intel JSON，Workers 发布 Findings 与最终候选，Reporter 读取最终快照。`
  ) : id === "skills" ? (
    "Agent 根据需要读取 /skills 下的能力包。未发生读取时不显示交互。"
  ) : id === "input" ? (
    "题目描述和附件索引进入黑板，原始文件只读提供给各 Agent。"
  ) : (
    "Reporter 根据最终黑板与只读证据生成 Markdown Writeup。"
  );
}
