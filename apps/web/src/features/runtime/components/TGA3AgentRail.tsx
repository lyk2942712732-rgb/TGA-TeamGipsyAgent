import { Bot, Boxes, FileText, Lightbulb } from "lucide-react";
import type { TGA3Agent } from "../../../runtime/tga3-runtime";
import { protocolLabel, roleLabel, sdkLabel, stateLabel, stateTone } from "../tga3-view";

const ORDER: Record<string, number> = { supervisor: 0, worker: 1, reporter: 2 };

export function TGA3AgentRail({ agents, selectedAgentId, onSelect }: { agents: TGA3Agent[]; selectedAgentId: string | null; onSelect: (agentId: string) => void }) {
  const ordered = [...agents].sort((a, b) => (ORDER[a.role] ?? 9) - (ORDER[b.role] ?? 9) || a.agent_id.localeCompare(b.agent_id));
  return <nav className="tga3-agent-rail" aria-label="任务 Agent">
    <header><div><span>AGENTS</span><h2>协作成员</h2></div><small>{agents.length}</small></header>
    <p className="tga3-agent-rail-note">Agent 之间不建立私有消息通道，只通过共享黑板同步经过验证的信息。</p>
    <div className="tga3-agent-list" role="listbox" aria-label="Agent 列表">
      {ordered.map((agent) => <button key={agent.agent_id} type="button" role="option" aria-selected={selectedAgentId === agent.agent_id} onClick={() => onSelect(agent.agent_id)}>
        <span className={`tga3-agent-icon role-${agent.role}`}>{roleIcon(agent)}</span>
        <span className="tga3-agent-copy">
          <b>{agent.display_name}</b>
          <small>{agent.agent_id} · {roleLabel(agent)}</small>
          <small>{agent.runtime_location === "container" ? "隔离容器" : "主服务"} · {sdkLabel(agent.sdk)}</small>
          <small className="tga3-agent-model">{agent.provider_id}/{agent.model_id}<em>{protocolLabel(agent.protocol)}</em></small>
          <span className={`tga3-state-pill tone-${stateTone(agent.actual_state)}`}><i />{stateLabel(agent.actual_state)}</span>
          {agent.actual_state === "failed" && agent.last_error ? <strong className="tga3-agent-error">{agent.last_error}</strong> : null}
        </span>
      </button>)}
    </div>
  </nav>;
}

function roleIcon(agent: TGA3Agent) {
  if (agent.role === "supervisor") return <Lightbulb size={17} />;
  if (agent.role === "reporter") return <FileText size={17} />;
  return agent.sdk === "claude_agent" ? <Boxes size={17} /> : <Bot size={17} />;
}
