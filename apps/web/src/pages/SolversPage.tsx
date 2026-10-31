import { Bot, Box, CheckCircle2, Database, RefreshCw, Save, Wrench, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { tga3ConfigApi, type AgentConfig, type AgentDefinition, type AgentsConfig, type ModelsConfig } from "../api/tga3-config";
import { enabledModelOptions } from "../api/model-options";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

const AGENT_ORDER = ["scout", "worker-openai", "worker-claude", "reporter"];
type ConfigTab = "model" | "prompt" | "tools";
export function SolversPage() {
  const [agents, setAgents] = useState<AgentsConfig | null>(null), [models, setModels] = useState<ModelsConfig | null>(null);
  const [definitions, setDefinitions] = useState<AgentDefinition[]>([]), [selectedId, setSelectedId] = useState("worker-openai");
  const [tab, setTab] = useState<ConfigTab>("model"), [busy, setBusy] = useState(false), [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState(""), [error, setError] = useState("");
  const [scoutSeconds, setScoutSeconds] = useState(300), [scoutTimeDirty, setScoutTimeDirty] = useState(false);
  async function load() {
    setError("");
    try { const [a, m, d] = await Promise.all([tga3ConfigApi.agents(), tga3ConfigApi.models(), tga3ConfigApi.agentDefinitions()]); setAgents(a); setModels(m); setDefinitions(d); setScoutSeconds(d.find(item => item.id === "scout")?.scout_timeout_seconds ?? 300); setScoutTimeDirty(false); setSelectedId(current => a.agents[current] ? current : AGENT_ORDER.find(id => a.agents[id]) ?? ""); setDirty(false); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取 Solver 配置"); }
  }
  useEffect(() => { void load(); }, []);
  const selected = agents?.agents[selectedId], definition = definitions.find(item => item.id === selectedId);
  const options = useMemo(() => enabledModelOptions(models).filter(item => selected?.runtime === "claude_cli" ? item.protocol === "anthropic" : item.protocol === "openai_responses"), [models, selected?.runtime]);
  function patch(value: Partial<AgentConfig>) { if (!agents || !selected) return; setAgents({ ...agents, agents: { ...agents.agents, [selectedId]: { ...selected, ...value } } }); setDirty(true); setMessage(""); }
  async function save() { if (!agents) return; if (scoutTimeDirty && (!Number.isInteger(scoutSeconds) || scoutSeconds < 60 || scoutSeconds > 600)) { setError("Scout 第一轮时长应为 60–600 秒的整数。"); return; } setBusy(true); setMessage(""); setError(""); try { if (scoutTimeDirty) { await tga3ConfigApi.saveScoutSettings(scoutSeconds); setScoutTimeDirty(false); } setAgents(await tga3ConfigApi.saveAgents(agents)); setDirty(false); setMessage("Solver 配置已保存。"); } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); } finally { setBusy(false); } }
  return <div className="ref-page solvers-page">
    <header className="ref-page-head"><div><h1>Solver 配置</h1><p>先侦察，两个 Worker 并行执行，最后生成报告。</p></div><div className="solver-head-actions"><button className="ref-secondary-button" disabled={busy} onClick={() => { if (!dirty || window.confirm("刷新将放弃尚未保存的配置，继续吗？")) void load(); }}><RefreshCw size={15} />刷新状态</button><button className="ref-primary-button" disabled={!agents || busy} onClick={() => void save()}><Save size={15} />{busy ? "保存中…" : "保存"}</button></div></header>
    {error ? <ErrorState description={error} actionLabel="重试" onAction={() => void load()} /> : null}
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    {!agents && !error ? <LoadingSkeleton label="正在读取 Solver" rows={6} /> : null}
    {agents ? <div className="workbench-solver-layout">
      <section className="workbench-team" aria-label="Solver 团队图"><div className="workbench-team-nodes">{AGENT_ORDER.filter(id => agents.agents[id]).map(id => {
        const agent = agents.agents[id], provider = models?.providers.find(item => item.id === agent.provider_id);
        return <button key={id} className="workbench-team-node" aria-label={`配置 ${agent.display_name}`} aria-pressed={selectedId === id} onClick={() => setSelectedId(id)}><span>{agent.role === "scout" ? "侦察阶段" : agent.role === "reporter" ? "报告阶段" : "并行执行"}</span><strong><Bot size={14} /> {agent.display_name}</strong><small>{sdkName(agent.runtime)}</small><small>{provider?.name ?? agent.provider_id} / {agent.model_id || "待配置"}</small></button>;
      })}</div><div className="workbench-team-bus"><Database size={14} />共享黑板：Intel → Findings → 最终候选</div></section>
      {selected ? <section className="ref-card workbench-solver-panel" aria-label={`${selected.display_name} Solver 配置`}>
        <header><div><h2>{selected.display_name}</h2><p>{roleName(selected.role)} / {sdkName(selected.runtime)}</p></div>{definition?.image_health ? <HealthDot status={definition.image_health.status} /> : null}</header>
        <div className="workbench-tabs" role="tablist" aria-label="Solver 配置内容">{([["model", "运行与模型"], ["prompt", "系统提示词"], ["tools", "工具与镜像"]] as const).map(([value, text]) => <button key={value} id={`solver-tab-${value}`} role="tab" aria-selected={tab === value} aria-controls={`solver-panel-${value}`} onClick={() => setTab(value)}>{text}</button>)}</div>
        <div className="workbench-solver-content" role="tabpanel" id={`solver-panel-${tab}`} aria-labelledby={`solver-tab-${tab}`}>
          {tab === "model" ? <>
            <label>供应商 / 已启用模型<select aria-label="供应商 / 已启用模型" value={options.some(o => o.provider.id === selected.provider_id && o.model.id === selected.model_id && o.protocol === selected.protocol) ? `${selected.provider_id}::${selected.model_id}::${selected.protocol}` : ""} disabled={!options.length} onChange={event => { const [provider_id, model_id, protocol] = event.target.value.split("::"); patch({ provider_id, model_id, protocol: protocol as AgentConfig["protocol"] }); }}><option value="" disabled>请选择已启用模型</option>{options.map(o => <option key={`${o.provider.id}::${o.model.id}::${o.protocol}`} value={`${o.provider.id}::${o.model.id}::${o.protocol}`}>{o.provider.name} / {o.model.name}</option>)}</select><small className="field-help">仅显示已启用且兼容当前 CLI 的模型。</small></label>
            <p className="workbench-execution-note">{selected.role === "scout" ? `第一轮执行并留存 artifacts；第二轮使用独立上下文提取 Intel，工具保存 JSON 后由系统导入黑板。第二轮独立预算 ${definition?.scout_extract_timeout_seconds ?? 180} 秒。` : selected.role === "reporter" ? "最终候选确定后，读取最终黑板与证据并生成报告。" : "持续推进 · 不限制轮数。异常恢复继续使用原 CLI 会话。"}</p>
            {selected.role === "scout" ? <label>第一轮侦察时长（秒）<input aria-label="第一轮侦察时长（秒）" type="number" min={60} max={600} step={1} value={scoutSeconds} onChange={event => { setScoutSeconds(Number(event.target.value)); setScoutTimeDirty(true); setDirty(true); setMessage(""); }} /><small className="field-help">默认 300 秒，范围 60–600 秒；第一轮结束或超时后进入第二轮。保存后用于新任务。</small></label> : null}
            {selected.role === "worker" ? <label>黑板同步间隔（秒）<input aria-label="黑板同步间隔（秒）" type="number" min={1} max={3600} value={selected.blackboard_interval_seconds ?? 60} onChange={event => patch({ blackboard_interval_seconds: Number(event.target.value) })} /><small className="field-help">有更新且达到间隔时，在下一次工具调用机会同步；提交最终候选前额外检查。</small></label> : null}
            <div className="workbench-definition"><span>角色<strong>{roleName(selected.role)}</strong></span><span>执行后端<strong>{sdkName(selected.runtime)}</strong></span><span>协议<strong>{selected.protocol === "anthropic" ? "Anthropic Messages" : "OpenAI Responses"}</strong></span></div>
          </> : tab === "prompt" ? <><h3>System Prompt</h3><textarea className="solver-prompt-editor" aria-label={`${selected.display_name} System Prompt`} rows={15} value={selected.system_prompt} onChange={event => patch({ system_prompt: event.target.value })} /><p className="field-help">用于后续启动的 Agent。</p></> : <><h3>任务工具</h3><div className="solver-tool-grid">{(definition?.tools ?? []).map(tool => <span key={tool}><Wrench size={13} />{tool}</span>)}</div>{definition?.image ? <ImageHealth definition={definition} /> : <p className="field-help">执行镜像尚未提供。</p>}</>}
        </div><footer><span role="status">{dirty ? "有未保存的更改" : "配置已保存"}</span><button className="ref-primary-button" disabled={busy} onClick={() => void save()}><Save size={15} />{busy ? "保存中…" : "保存配置"}</button></footer>
      </section> : null}
    </div> : null}
  </div>;
}
function HealthDot({ status }: { status: string }) { return <span className={`image-health-dot ${status}`}>{status === "healthy" ? <CheckCircle2 size={13} /> : <XCircle size={13} />}{status === "healthy" ? "镜像可用" : status === "missing" ? "镜像缺失" : "镜像不可用"}</span>; }
function ImageHealth({ definition }: { definition: AgentDefinition }) { const health = definition.image_health; return <section className="worker-image-card"><header><div><Box size={18} /><span><small>执行镜像</small><strong>{definition.image}</strong></span></div>{health ? <HealthDot status={health.status} /> : null}</header><dl><div><dt>状态</dt><dd>{health?.detail ?? "未检查"}</dd></div>{health?.image_id ? <div><dt>镜像 ID</dt><dd>{health.image_id}</dd></div> : null}{health?.size_bytes ? <div><dt>大小</dt><dd>{(health.size_bytes / 1024 ** 3).toFixed(2)} GB</dd></div> : null}</dl></section>; }
function roleName(value: string) { return ({ scout: "侦察", worker: "执行", reporter: "报告" } as Record<string, string>)[value] ?? value; }
function sdkName(value: string) { return value === "claude_cli" ? "Claude Code CLI" : "Codex CLI"; }
