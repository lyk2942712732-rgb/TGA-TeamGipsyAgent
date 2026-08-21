import { Bot, Box, CheckCircle2, Cpu, RefreshCw, Save, Wrench, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { tga3ConfigApi, type AgentDefinition, type AgentsConfig, type ModelsConfig } from "../api/tga3-config";

type Tab = "overview" | "model" | "prompt" | "tools";

export function SolversPage() {
  const [agents, setAgents] = useState<AgentsConfig | null>(null);
  const [models, setModels] = useState<ModelsConfig | null>(null);
  const [definitions, setDefinitions] = useState<AgentDefinition[]>([]);
  const [selectedId, setSelectedId] = useState("supervisor");
  const [tab, setTab] = useState<Tab>("overview");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function load() {
    setMessage("");
    try {
      const [nextAgents, nextModels, nextDefinitions] = await Promise.all([tga3ConfigApi.agents(), tga3ConfigApi.models(), tga3ConfigApi.agentDefinitions()]);
      setAgents(nextAgents); setModels(nextModels); setDefinitions(nextDefinitions);
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "无法读取 Agent 配置"); }
  }
  useEffect(() => { void load(); }, []);

  const selected = agents?.agents[selectedId] ?? null;
  const definition = definitions.find((item) => item.id === selectedId) ?? null;
  const availableModels = useMemo(() => (models?.providers ?? []).flatMap((provider) => provider.models.filter(() => selected?.runtime === "claude_agent" ? provider.protocol === "anthropic" : provider.protocol !== "anthropic").map((model) => ({ provider, model }))), [models, selected?.runtime]);

  function patch(value: Partial<NonNullable<typeof selected>>) {
    if (!agents || !selected) return;
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
    <header className="ref-page-head"><div><h1>Solver 配置</h1><p>Agent 身份固定；在这里维护模型、周期轮次和 System Prompt，并查看实际工具与 Worker 镜像。</p></div><div className="solver-head-actions"><button className="ref-secondary-button" onClick={() => void load()}><RefreshCw size={16} />刷新状态</button><button className="ref-primary-button" disabled={!agents || busy} onClick={() => void save()}><Save size={16} />{busy ? "保存中…" : "保存"}</button></div></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    <div className="solver-settings-layout ref-fill">
      <aside className="solver-agent-list" aria-label="Agent 列表">{Object.entries(agents?.agents ?? {}).map(([id, item]) => {
        const info = definitions.find((value) => value.id === id);
        return <button key={id} className={selectedId === id ? "selected" : ""} onClick={() => { setSelectedId(id); setTab("overview"); }}>
          <span className="solver-list-icon"><Bot size={18} /></span><span><strong>{item.display_name}</strong><small>{roleName(item.role)} · {sdkName(item.runtime)}</small></span>
          {info?.image_health ? <HealthDot status={info.image_health.status} /> : <span className="host-agent-chip">主服务</span>}
        </button>;
      })}</aside>
      {selected ? <main className="ref-card solver-settings-detail">
        <header className="solver-settings-title"><div><span className="solver-large-icon"><Bot size={23} /></span><div><h2>{selected.display_name}</h2><p>{selectedId} · {roleName(selected.role)}</p></div></div><span className="fixed-definition-chip">固定身份</span></header>
        <nav className="detail-tabs">{([['overview','概览'],['model','模型'],['prompt','System Prompt'],['tools','工具与镜像']] as Array<[Tab,string]>).map(([id,label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}</nav>
        {tab === "overview" ? <section className="solver-detail-section"><h3>运行定义</h3><div className="solver-readonly-grid"><Info label="名称" value={selected.display_name} /><Info label="角色" value={roleName(selected.role)} /><Info label="Agent SDK" value={sdkName(selected.runtime)} /><label>每周期最大轮数<input type="number" min={1} max={50} value={selected.max_turns_per_cycle} onChange={(event) => patch({ max_turns_per_cycle: Number(event.target.value) })} /></label></div><p className="field-help">名称、角色和 SDK 属于程序定义，不可在配置界面修改。</p></section> : null}
        {tab === "model" ? <section className="solver-detail-section"><h3><Cpu size={17} />模型绑定</h3><label className="wide">供应商 / 模型<select value={`${selected.provider_id}::${selected.model_id}`} onChange={(event) => { const [provider_id, model_id] = event.target.value.split("::"); patch({ provider_id, model_id }); }}>{availableModels.map(({ provider, model }) => <option key={`${provider.id}::${model.id}`} value={`${provider.id}::${model.id}`}>{provider.name} / {model.name}</option>)}</select></label><p className="field-help">供应商、密钥和模型定义在 Models 页面维护。</p></section> : null}
        {tab === "prompt" ? <section className="solver-detail-section"><h3>System Prompt</h3><textarea className="solver-prompt-editor" rows={18} value={selected.system_prompt} onChange={(event) => patch({ system_prompt: event.target.value })} /><p className="field-help">内容直接保存到 config/agents.json。</p></section> : null}
        {tab === "tools" ? <section className="solver-detail-section"><h3><Wrench size={17} />可用工具</h3><div className="solver-tool-grid">{(definition?.tools ?? []).map((tool) => <span key={tool}><Wrench size={14} />{tool}</span>)}</div>{definition?.image ? <ImageHealth definition={definition} /> : <div className="host-runtime-card"><Box size={20} /><div><strong>主服务运行</strong><p>{selected.display_name} 不创建任务容器。</p></div></div>}</section> : null}
      </main> : null}
    </div>
  </div>;
}

function Info({ label, value }: { label: string; value: string }) { return <div className="solver-readonly-field"><span>{label}</span><strong>{value}</strong></div>; }
function HealthDot({ status }: { status: string }) { return <span className={`image-health-dot ${status}`}>{status === "healthy" ? <CheckCircle2 size={14} /> : <XCircle size={14} />}{status === "healthy" ? "健康" : status === "missing" ? "缺失" : "不可用"}</span>; }
function ImageHealth({ definition }: { definition: AgentDefinition }) {
  const health = definition.image_health;
  return <section className="worker-image-card"><header><div><Box size={20} /><span><small>Worker 镜像</small><strong>{definition.image}</strong></span></div>{health ? <HealthDot status={health.status} /> : null}</header><dl><div><dt>状态</dt><dd>{health?.detail ?? "未检查"}</dd></div>{health?.image_id ? <div><dt>镜像 ID</dt><dd>{health.image_id}</dd></div> : null}{health?.size_bytes ? <div><dt>大小</dt><dd>{formatBytes(health.size_bytes)}</dd></div> : null}</dl></section>;
}
function roleName(value: string) { return ({ supervisor: "Supervisor / 顾问", worker: "Worker / 执行", reporter: "Reporter / 报告" } as Record<string,string>)[value] ?? value; }
function sdkName(value: string) { return value === "claude_agent" ? "Claude Agent SDK" : "OpenAI Agents SDK"; }
function formatBytes(value: number) { return value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(0)} MB` : `${(value / 1024 ** 3).toFixed(2)} GB`; }
