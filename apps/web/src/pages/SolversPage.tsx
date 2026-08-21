import { Bot, Check, Cpu, Save, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { tga3ConfigApi, type AgentsConfig, type ModelsConfig } from "../api/tga3-config";

type Tab = "overview" | "model" | "prompt";

export function SolversPage() {
  const [agents, setAgents] = useState<AgentsConfig | null>(null);
  const [models, setModels] = useState<ModelsConfig | null>(null);
  const [selectedId, setSelectedId] = useState("supervisor");
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<Tab>("overview");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => { void Promise.all([tga3ConfigApi.agents(), tga3ConfigApi.models()]).then(([nextAgents, nextModels]) => { setAgents(nextAgents); setModels(nextModels); }).catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "无法读取 Agent 配置")); }, []);
  const rows = useMemo(() => Object.entries(agents?.agents ?? {}).filter(([id, item]) => `${id} ${item.display_name} ${item.role}`.toLowerCase().includes(search.toLowerCase())), [agents, search]);
  const selected = agents?.agents[selectedId] ?? null;
  const availableModels = useMemo(() => (models?.providers ?? []).flatMap((provider) => provider.models.filter(() => selected?.runtime === "claude_agent" ? provider.protocol === "anthropic" : provider.protocol !== "anthropic").map((model) => ({ provider, model }))), [models, selected?.runtime]);

  function patch(value: Partial<NonNullable<typeof selected>>) {
    if (!agents || !selected) return;
    setAgents({ ...agents, agents: { ...agents.agents, [selectedId]: { ...selected, ...value } } });
  }

  async function save() {
    if (!agents) return;
    setBusy(true); setMessage("");
    try { setAgents(await tga3ConfigApi.saveAgents(agents)); setMessage("已写入 config/agents.json，新启动的调用将使用该配置。"); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setBusy(false); }
  }

  return <div className="ref-page solvers-page">
    <header className="ref-page-head"><div><span className="eyebrow">AGENT DEFINITIONS</span><h1>Solver 配置</h1><p>在原 Solver 页面分别配置四个 Agent 的模型、轮次与系统提示词。</p></div><button className="ref-primary-button" disabled={!agents || busy} onClick={() => void save()}><Save size={16} />{busy ? "保存中…" : "保存 agents.json"}</button></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    <div className="ref-master-detail solvers-layout ref-fill">
      <section className="ref-card solver-catalog-panel">
        <header className="ref-card-head"><h2>Agent</h2><span>{rows.length}</span></header>
        <label className="ref-search"><Search size={16} /><input aria-label="搜索 Agent" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称或角色…" /></label>
        <div className="solver-catalog-list">{rows.map(([id, item]) => <button key={id} className={selectedId === id ? "selected" : ""} onClick={() => setSelectedId(id)}><span className="solver-avatar"><Bot size={18} /></span><span><strong>{item.display_name}</strong><small>{id} · {roleName(item.role)}</small></span><em>{item.runtime === "claude_agent" ? "Claude Agent SDK" : "OpenAI Agents SDK"}</em></button>)}</div>
      </section>
      {selected ? <section className="ref-detail-panel solver-definition-detail">
        <header className="ref-detail-head"><div className="ref-detail-title"><span className="solver-avatar"><Bot size={20} /></span><div><h2>{selected.display_name}</h2><p>{selectedId}</p></div></div><span className="ref-chip tone-ok"><Check size={12} />已启用</span></header>
        <nav className="detail-tabs">{([["overview", "概览"], ["model", "模型"], ["prompt", "System Prompt"]] as Array<[Tab, string]>).map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}</nav>
        {tab === "overview" ? <section className="solver-detail-section"><h3>运行身份</h3><div className="field-grid"><label>显示名称<input value={selected.display_name} onChange={(event) => patch({ display_name: event.target.value })} /></label><label>角色<input value={roleName(selected.role)} disabled /></label><label>Agent SDK<input value={selected.runtime === "claude_agent" ? "Claude Agent SDK" : "OpenAI Agents SDK"} disabled /></label><label>每周期最大轮数<input type="number" min={1} max={50} value={selected.max_turns_per_cycle} onChange={(event) => patch({ max_turns_per_cycle: Number(event.target.value) })} /></label></div><p className="skill-summary">Supervisor 只提供黑板建议；两个 Worker 在容器运行；Reporter 在最终候选出现后生成 Markdown writeup。</p></section> : null}
        {tab === "model" ? <section className="solver-detail-section"><h3><Cpu size={16} />角色模型</h3><label className="wide">供应商 / 模型<select value={`${selected.provider_id}::${selected.model_id}`} onChange={(event) => { const [provider_id, model_id] = event.target.value.split("::"); patch({ provider_id, model_id }); }}>{availableModels.map(({ provider, model }) => <option key={`${provider.id}::${model.id}`} value={`${provider.id}::${model.id}`}>{provider.name} / {model.name}</option>)}</select></label><p className="skill-summary">供应商、密钥和模型本身请在“模型供应商”页面维护；这里仅保存 Agent 与模型的绑定。</p></section> : null}
        {tab === "prompt" ? <section className="solver-detail-section"><h3>系统提示词</h3><textarea className="solver-prompt-editor" rows={20} value={selected.system_prompt} onChange={(event) => patch({ system_prompt: event.target.value })} /><p className="skill-summary">该字段直接对应 config/agents.json 的 system_prompt，没有其他隐藏提示词来源。</p></section> : null}
      </section> : null}
    </div>
  </div>;
}

function roleName(value: string) { return ({ supervisor: "Supervisor / 顾问", worker: "Worker / 执行", reporter: "Reporter / 报告" } as Record<string, string>)[value] ?? value; }
