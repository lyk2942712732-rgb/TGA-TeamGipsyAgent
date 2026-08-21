import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Bot, Boxes, FileText, ShieldCheck, UserRound, Wrench } from "lucide-react";
import { orderedEvents } from "../models/selectors";
import type { RuntimeEvent, RuntimeStore } from "../models/types";
import { statusDefinition } from "../../../shared/status";

type TopologyNodeData = {
  label: string;
  role: string;
  solverId: string | null;
  status: string;
  summary: string;
  icon: ReactNode;
};

type Interaction = {
  event: RuntimeEvent;
  source: string;
  target: string;
  label: string;
};

const ROLE_LAYOUT = {
  operator: { x: 10, y: 18 },
  supervisor: { x: 310, y: 18 },
  worker: { x: 90, y: 205 },
  reviewer: { x: 530, y: 205 },
  tools: { x: 10, y: 392 },
  reporter: { x: 310, y: 392 },
} as const;

const BASE_EDGES: Array<[string, string, string, string]> = [
  ["supervisor-worker", "supervisor", "worker", "分配 / 反馈"],
  ["worker-reviewer", "worker", "reviewer", "证据包"],
  ["reviewer-supervisor", "reviewer", "supervisor", "审查结论"],
  ["supervisor-reporter", "supervisor", "reporter", "完成决策"],
  ["worker-tools", "worker", "tools", "工具调用"],
  ["supervisor-operator", "supervisor", "operator", "询问 / 回答"],
];

export function RuntimeTopology({ store, readonly = false, onSelectSolver = () => undefined }: {
  store: RuntimeStore;
  readonly?: boolean;
  onSelectSolver?: (solverId: string) => void;
}) {
  const latest = useMemo(() => latestInteraction(store), [store.latestSeq, store.eventsBySeq]);
  const [flashingSeq, setFlashingSeq] = useState<number | null>(latest?.event.seq ?? null);
  useEffect(() => {
    if (!latest) return;
    setFlashingSeq(latest.event.seq);
    if (readonly) return;
    const timer = window.setTimeout(() => setFlashingSeq((value) => value === latest.event.seq ? null : value), 1700);
    return () => window.clearTimeout(timer);
  }, [latest?.event.seq, readonly]);
  const active = latest && (readonly || flashingSeq === latest.event.seq) ? latest : null;

  const nodes = useMemo<Node<TopologyNodeData>[]>(() => {
    const role = (id: string, label: string, icon: ReactNode): Node<TopologyNodeData> => {
      const solver = store.solversById[id] ?? Object.values(store.solversById).find((item) => item.orchestrationRole === id);
      const operatorAttention = id === "operator" && ["awaiting_approval", "awaiting_user_input"].includes(store.session.status);
      const toolsActive = id === "tools" && active && [active.source, active.target].includes("tools");
      return {
        id,
        type: "runtimeRole",
        position: ROLE_LAYOUT[id as keyof typeof ROLE_LAYOUT],
        data: {
          label,
          role: id,
          solverId: solver?.solverId ?? null,
          status: solver?.status ?? (operatorAttention ? store.session.status : toolsActive ? "running" : "not_started"),
          summary: solver?.currentSummary
            || (operatorAttention ? (store.session.userInputRequest?.question || "等待操作员处理") : null)
            || (toolsActive ? active.label : null)
            || (solver ? "等待上游结果" : "尚未进入本次任务"),
          icon,
        },
      };
    };
    return [
      role("operator", "Operator", <UserRound size={18} />),
      role("supervisor", "Supervisor", <ShieldCheck size={18} />),
      role("worker", "Worker", <Bot size={18} />),
      role("reviewer", "Reviewer", <Boxes size={18} />),
      role("tools", "Kali / Tools", <Wrench size={18} />),
      role("reporter", "Reporter", <FileText size={18} />),
    ];
  }, [active?.event.seq, store.session.status, store.session.userInputRequest?.question, store.solversById]);

  const edges = useMemo<Edge[]>(() => BASE_EDGES.map(([id, source, target, label]) => {
    const highlighted = active?.source === source && active.target === target;
    const reverse = active?.source === target && active.target === source;
    return {
      id,
      source: reverse ? target : source,
      target: reverse ? source : target,
      label: highlighted || reverse ? active?.label : label,
      animated: Boolean(highlighted || reverse),
      className: highlighted || reverse ? "topology-edge-active" : "topology-edge-idle",
      style: { strokeWidth: highlighted || reverse ? 3 : 1.4 },
      labelStyle: { fontSize: 11, fontWeight: highlighted || reverse ? 700 : 500 },
    };
  }), [active?.event.seq, active?.source, active?.target]);

  return <section className="runtime-topology" aria-labelledby="runtime-topology-title">
    <header className="runtime-section-title">
      <div><span>LIVE EXECUTION GRAPH</span><h3 id="runtime-topology-title">运行拓扑</h3></div>
      <small>{readonly ? `回放至事件 #${store.latestSeq}` : "交互只闪现，不堆积"}</small>
    </header>
    <div className="topology-current-event" aria-live="polite">
      {latest ? <><b>{active ? "正在交互" : "最近交互"}</b><span>{latest.label}</span><code>#{latest.event.seq} {latest.event.type}</code></> : <><b>尚未开始交互</b><span>任务开始后将在这里显示 Solver 间的数据流。</span></>}
    </div>
    <div className="runtime-topology-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={{ runtimeRole: RuntimeRoleNode }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnDoubleClick={false}
        minZoom={0.65}
        maxZoom={1.35}
        fitView
        fitViewOptions={{ padding: 0.16 }}
        onNodeClick={(_event, node) => {
          const solverId = (node.data as TopologyNodeData).solverId;
          if (solverId) onSelectSolver(solverId);
        }}
      >
        <Background gap={22} size={1} color="#e5e7eb" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
    <footer className="topology-legend"><span><i className="running" />执行中</span><span><i className="waiting" />等待上游</span><span><i className="attention" />等待人工处理</span><span><i className="complete" />已完成</span></footer>
  </section>;
}

function RuntimeRoleNode({ data }: NodeProps<Node<TopologyNodeData>>) {
  const definition = statusDefinition(data.status);
  const waitingAttention = ["awaiting_approval", "awaiting_user_input"].includes(data.status);
  return <article className={`runtime-topology-node role-${data.role} status-${data.status}`}>
    <Handle type="target" position={Position.Top} />
    <Handle type="target" position={Position.Left} id="left-target" />
    <header><span>{data.icon}</span><div><b>{data.label}</b><small>{data.role}</small></div><em className={`tone-${definition.tone}`}>{definition.label}</em></header>
    <p title={data.summary}>{data.summary}</p>
    {waitingAttention ? <strong>需要操作员处理</strong> : null}
    <Handle type="source" position={Position.Bottom} />
    <Handle type="source" position={Position.Right} id="right-source" />
  </article>;
}

function latestInteraction(store: RuntimeStore): Interaction | null {
  const events = orderedEvents(store);
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const mapped = interaction(events[index]);
    if (mapped) return mapped;
  }
  return null;
}

function interaction(event: RuntimeEvent): Interaction | null {
  const action = String(event.payload.action ?? "");
  const mapping: Record<string, [string, string, string]> = {
    PLAN_CREATED: ["supervisor", "worker", "Supervisor 已生成 Plan"],
    INTENT_STARTED: ["supervisor", "worker", "Supervisor 分配当前 Intent"],
    INTENT_RETRY_REQUESTED: ["supervisor", "worker", "Supervisor 携反馈要求重试"],
    WORKER_ATTEMPT_COMPLETED: ["worker", "reviewer", "Worker 提交调查结果和证据包"],
    REVIEW_COMPLETED: ["reviewer", "supervisor", "Reviewer 返回审查结论"],
    REPORT_GENERATED: ["reporter", "supervisor", "Reporter 返回最终报告"],
    TOOL_ACTION_REQUESTED: ["worker", "tools", `Worker 请求 ${String(event.payload.tool_name ?? "工具")}`],
    APPROVAL_REQUESTED: ["worker", "operator", `请求批准 ${String(event.payload.tool_name ?? "工具调用")}`],
    TOOL_COMPLETED: ["tools", "worker", `${String(event.payload.tool_name ?? "工具")} 返回结果`],
    USER_INPUT_REQUIRED: ["supervisor", "operator", "Supervisor 请求用户输入"],
    USER_INPUT_RECEIVED: ["operator", "supervisor", "用户回答 Supervisor"],
  };
  const value = event.type === "SUPERVISOR_DECIDED"
    ? (action === "finish" ? ["supervisor", "reporter", "Supervisor 请求生成报告"] : ["supervisor", "worker", `Supervisor 决策：${action || "继续"}`])
    : mapping[event.type];
  return value ? { event, source: value[0], target: value[1], label: value[2] } : null;
}
