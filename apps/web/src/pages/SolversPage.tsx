import { Bot, Box, BrainCircuit, CheckCircle2, Cpu, Database, Library, RefreshCw, Save, UserRound, Wrench, X, XCircle } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { tga3ConfigApi, type AgentConfig, type AgentDefinition, type AgentsConfig, type ModelsConfig } from "../api/tga3-config";

import { enabledModelOptions } from "../api/model-options";

type ModelOption = ReturnType<typeof enabledModelOptions>[number];

const AGENT_ORDER = ["scout", "worker-openai", "worker-claude", "reporter"];

export function SolversPage() {
  const [agents, setAgents] = useState<AgentsConfig | null>(null);
  const [models, setModels] = useState<ModelsConfig | null>(null);
  const [definitions, setDefinitions] = useState<AgentDefinition[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function load() {
    setMessage("");
    try {
      const [nextAgents, nextModels, nextDefinitions] = await Promise.all([tga3ConfigApi.agents(), tga3ConfigApi.models(), tga3ConfigApi.agentDefinitions()]);
      setAgents(nextAgents); setModels(nextModels); setDefinitions(nextDefinitions);
      setSelectedId((current) => current && nextAgents.agents[current] ? current : null);
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "无法读取 Agent 配置"); }
  }
  useEffect(() => { void load(); }, []);

  const selected = selectedId ? agents?.agents[selectedId] ?? null : null;
  const definition = selectedId ? definitions.find((item) => item.id === selectedId) ?? null : null;
  const availableModels = useMemo(() => enabledModelOptions(models), [models]);

  function patch(value: Partial<AgentConfig>) {
    if (!agents || !selected || !selectedId) return;
    setAgents({ ...agents, agents: { ...agents.agents, [selectedId]: { ...selected, ...value } } });
  }
  async function save() {
    if (!agents) return;
    setBusy(true); setMessage("");
    try { setAgents(await tga3ConfigApi.saveAgents(agents)); setMessage("已保存 Agent 的模型、轮次与系统提示词。"); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setBusy(false); }
  }

  return <div className="ref-page solvers-page">
    <header className="ref-page-head"><div><h1>Solver 配置</h1><p>从团队图选择 Agent，在右侧集中维护其运行定义、模型、System Prompt、工具与镜像。</p></div><div className="solver-head-actions"><button className="ref-secondary-button" onClick={() => void load()}><RefreshCw size={16} />刷新状态</button><button className="ref-primary-button" disabled={!agents || busy} onClick={() => void save()}><Save size={16} />{busy ? "保存中…" : "保存"}</button></div></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    <div className="solver-team-layout ref-fill" data-panel={Boolean(selected)}>
      <SolverTeamGraph agents={agents} models={models} definitions={definitions} selectedId={selectedId} onSelect={setSelectedId} />
      {selected && selectedId ? <SolverConfigPanel agentId={selectedId} agent={selected} definition={definition} availableModels={availableModels} busy={busy} onPatch={patch} onSave={() => void save()} onClose={() => setSelectedId(null)} /> : null}
    </div>
  </div>;
}

function SolverTeamGraph({ agents, models, definitions, selectedId, onSelect }: { agents: AgentsConfig | null; models: ModelsConfig | null; definitions: AgentDefinition[]; selectedId: string | null; onSelect: (agentId: string) => void }) {
  const ordered = AGENT_ORDER.flatMap((id) => agents?.agents[id] ? [[id, agents.agents[id]] as const] : []);
  return <section className="solver-team-graph ref-card" aria-label="Solver 团队图" data-focused={Boolean(selectedId)}>
    <header><div><span className="eyebrow">STATIC TEAM TOPOLOGY</span><h2>团队图</h2></div><p>{selectedId ? "已聚焦当前 Agent；点击其他 Agent 可切换配置。" : "点击任一 Agent 查看并编辑完整 Solver 配置。"}</p></header>
    <div className="solver-team-canvas">
      <svg className="solver-team-edges" viewBox="0 0 1000 600" preserveAspectRatio="none" aria-hidden="true">
        <line className={edgeClass(selectedId)} x1="500" y1="55" x2="500" y2="300" />
        <line className={edgeClass(selectedId)} x1="300" y1="315" x2="500" y2="300" />
        <line className={edgeClass(selectedId)} x1="700" y1="315" x2="500" y2="300" />
        <line className={edgeClass(selectedId)} x1="500" y1="415" x2="500" y2="300" />
        <line className={`model ${edgeClass(selectedId)}`} x1="90" y1="315" x2="300" y2="315" />
        <line className={`model ${edgeClass(selectedId)}`} x1="910" y1="315" x2="700" y2="315" />
        <line className={`model ${edgeClass(selectedId)}`} x1="400" y1="415" x2="600" y2="415" />
      </svg>
      <TeamNode className="user" dimmed={Boolean(selectedId)} icon={<UserRound size={20} />} title="用户" meta="任务与原始附件" />
      <TeamNode className="blackboard" dimmed={Boolean(selectedId)} icon={<Database size={21} />} title="黑板" meta="Agent 共享协作区" />
      <TeamNode className="skills" dimmed={Boolean(selectedId)} icon={<Library size={20} />} title="Skills" meta="按需读取能力" />
      {ordered.map(([id, agent]) => {
        const definition = definitions.find((item) => item.id === id);
        const model = modelLabel(agent, models);
        const focused = selectedId === id;
        return <div key={id} className={`solver-team-lane lane-${id}`} data-dimmed={Boolean(selectedId && !focused)}>
          <TeamNode className="model" dimmed={Boolean(selectedId)} icon={<BrainCircuit size={18} />} title={model.name} meta={model.provider} />
          <button className={`solver-team-agent role-${agent.role}`} data-selected={focused} data-dimmed={Boolean(selectedId && !focused)} onClick={() => onSelect(id)} aria-label={`配置 ${agent.display_name}`}>
            <span>{<Bot size={19} />}</span>
            <div><b>{agent.display_name}</b><small>{roleShort(agent.role)} · {sdkShort(agent.runtime)}</small></div>
            {definition?.image_health ? <i className={`health-${definition.image_health.status}`} title={definition.image_health.detail} /> : <i className="health-host" title="主服务运行" />}
          </button>
        </div>;
      })}
    </div>
  </section>;
}

function TeamNode({ className, dimmed, icon, title, meta }: { className: string; dimmed: boolean; icon: ReactNode; title: string; meta: string }) {
  return <article className={`solver-team-node ${className}`} data-dimmed={dimmed}><span>{icon}</span><div><b title={title}>{title}</b><small>{meta}</small></div></article>;
}

function SolverConfigPanel({ agentId, agent, definition, availableModels, busy, onPatch, onSave, onClose }: { agentId: string; agent: AgentConfig; definition: AgentDefinition | null; availableModels: ModelOption[]; busy: boolean; onPatch: (value: Partial<AgentConfig>) => void; onSave: () => void; onClose: () => void }) {
  const compatibleModels = availableModels.filter(({ protocol }) => modelCompatible(agent.runtime, protocol));
  return <aside className="ref-card solver-config-drawer" aria-label={`${agent.display_name} Solver 配置`}>
    <header className="solver-settings-title"><div><span className="solver-large-icon"><Bot size={23} /></span><div><span className="eyebrow">SOLVER CONFIGURATION</span><h2>{agent.display_name}</h2><p>{agentId} · {roleName(agent.role)}</p></div></div><button className="icon-button" aria-label="关闭 Solver 配置" onClick={onClose}><X size={19} /></button></header>
    <div className="solver-config-scroll">
      <section className="solver-detail-section"><header><span>01</span><h3>概览</h3></header><div className="solver-readonly-grid"><Info label="名称" value={agent.display_name} /><Info label="角色" value={roleName(agent.role)} /><Info label="执行后端" value={sdkName(agent.runtime)} />{agent.runtime === "codex_cli" ? <Info label={agent.role === "scout" ? "执行方式" : "内部循环"} value={agent.role === "scout" ? `单次侦察 · 总预算 ${definition?.scout_timeout_seconds ?? 300} 秒` : "由 Codex CLI 管理"} /> : <label>每次执行最大轮数<input type="number" min={1} max={50} value={agent.max_turns_per_cycle} onChange={(event) => onPatch({ max_turns_per_cycle: Number(event.target.value) })} /></label>}</div><p className="field-help">名称、角色和执行后端属于程序定义，不可在配置界面修改。</p></section>
      <section className="solver-detail-section"><header><span>02</span><h3><Cpu size={17} />模型</h3></header><label className="wide">供应商 / 已启用模型<select value={compatibleModels.some(({ provider, model, protocol }) => provider.id === agent.provider_id && model.id === agent.model_id && protocol === agent.protocol) ? `${agent.provider_id}::${agent.model_id}::${agent.protocol}` : ""} disabled={!compatibleModels.length} onChange={(event) => { const [provider_id, model_id, protocol] = event.target.value.split("::"); onPatch({ provider_id, model_id, protocol: protocol as AgentConfig["protocol"] }); }}><option value="" disabled>请选择已启用模型</option>{compatibleModels.length ? compatibleModels.map(({ provider, model, protocol }) => <option key={`${provider.id}::${model.id}::${protocol}`} value={`${provider.id}::${model.id}::${protocol}`}>{provider.name} / {model.name}</option>) : <option value="">没有可用的兼容模型</option>}</select></label><p className="field-help">仅显示对应协议端点中已启用的模型，协议由执行后端自动确定。</p></section>
      <section className="solver-detail-section"><header><span>03</span><h3>System Prompt</h3></header><textarea className="solver-prompt-editor" aria-label={`${agent.display_name} System Prompt`} rows={12} value={agent.system_prompt} onChange={(event) => onPatch({ system_prompt: event.target.value })} /><p className="field-help">内容直接保存到 config/agents.json。</p></section>
      <section className="solver-detail-section"><header><span>04</span><h3><Wrench size={17} />工具与镜像</h3></header><div className="solver-tool-grid">{(definition?.tools ?? []).map((tool) => <span key={tool}><Wrench size={14} />{tool}</span>)}</div>{definition?.image ? <ImageHealth definition={definition} /> : <div className="host-runtime-card"><Box size={20} /><div><strong>主服务运行</strong><p>{agent.display_name} 不创建任务容器。</p></div></div>}</section>
    </div>
    <footer><button className="ref-secondary-button" onClick={onClose}>返回团队图</button><button className="ref-primary-button" disabled={busy} onClick={onSave}><Save size={15} />{busy ? "保存中…" : "保存配置"}</button></footer>
  </aside>;
}

function Info({ label, value }: { label: string; value: string }) { return <div className="solver-readonly-field"><span>{label}</span><strong>{value}</strong></div>; }
function HealthDot({ status }: { status: string }) { return <span className={`image-health-dot ${status}`}>{status === "healthy" ? <CheckCircle2 size={14} /> : <XCircle size={14} />}{status === "healthy" ? "健康" : status === "missing" ? "缺失" : "不可用"}</span>; }
function ImageHealth({ definition }: { definition: AgentDefinition }) {
  const health = definition.image_health;
  return <section className="worker-image-card"><header><div><Box size={20} /><span><small>执行镜像</small><strong>{definition.image}</strong></span></div>{health ? <HealthDot status={health.status} /> : null}</header><dl><div><dt>状态</dt><dd>{health?.detail ?? "未检查"}</dd></div>{health?.image_id ? <div><dt>镜像 ID</dt><dd>{health.image_id}</dd></div> : null}{health?.size_bytes ? <div><dt>大小</dt><dd>{formatBytes(health.size_bytes)}</dd></div> : null}</dl></section>;
}
function modelLabel(agent: AgentConfig, models: ModelsConfig | null) { const provider = models?.providers.find((item) => item.id === agent.provider_id); return { provider: provider?.name ?? (agent.provider_id || "请选择供应商"), name: provider?.endpoints[agent.protocol]?.models.find((item) => item.id === agent.model_id)?.name ?? (agent.model_id || "待配置") }; }
function edgeClass(selectedId: string | null) { return selectedId ? "dimmed" : ""; }
function roleName(value: string) { return ({ scout: "Scout / 侦察", worker: "Worker / 执行", reporter: "Reporter / 报告" } as Record<string,string>)[value] ?? value; }
function roleShort(value: string) { return ({ scout: "侦察", worker: "执行", reporter: "报告" } as Record<string,string>)[value] ?? value; }
function sdkName(value: string) { return ({ claude_cli: "Claude Code CLI", codex_cli: "Codex CLI" } as Record<string, string>)[value] ?? value; }
function sdkShort(value: string) { return value === "claude_cli" ? "Claude CLI" : value === "codex_cli" ? "Codex CLI" : "预留"; }
function formatBytes(value: number) { return value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(0)} MB` : `${(value / 1024 ** 3).toFixed(2)} GB`; }
function modelCompatible(runtime: "codex_cli" | "claude_cli", protocol: "openai_responses" | "openai_chat_completions" | "anthropic") { return runtime === "claude_cli" ? protocol === "anthropic" : protocol === "openai_responses"; }
