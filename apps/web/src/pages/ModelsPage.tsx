import { FormEvent, useEffect, useState } from "react";
import { Check, ChevronDown, ChevronRight, KeyRound, Plus, RefreshCw, Search, Server, Trash2, X } from "lucide-react";
import {
  addProviderAPIKey, createModelProvider, deleteModelProvider, discoverProviderModels, fetchProviderCatalog,
  selectProviderAPIKey, setProviderModelsEnabled, updateProviderEndpoints,
  type ModelProvider, type ProviderCatalog,
} from "../api/tga3-models";
import type { ProviderEndpoint, ProviderEndpoints, ProviderProtocol } from "../api/tga3-config";

type ProviderDraft = { preset_id: string; name: string; endpoints: ProviderEndpoints; api_key: string };
const EMPTY_PROVIDER: ProviderDraft = { preset_id: "custom", name: "", endpoints: { openai_responses: { base_url: "", models: [] } }, api_key: "" };
const PROTOCOLS: ProviderProtocol[] = ["openai_responses", "anthropic", "openai_chat_completions"];

export function ModelsPage({ onConfiguredChange }: { onConfiguredChange?: (configured: boolean) => void }) {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<ProviderDraft>(EMPTY_PROVIDER);
  const [editingEndpoints, setEditingEndpoints] = useState(false);
  const [endpoints, setEndpoints] = useState<ProviderEndpoints>({});
  const [newKey, setNewKey] = useState("");
  const [newKeyLabel, setNewKeyLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeProtocol, setActiveProtocol] = useState<ProviderProtocol>("openai_responses");
  const [message, setMessage] = useState("");
  const selected = catalog?.providers.find((provider) => provider.id === selectedId) ?? null;
  const protocolToShow = selected?.endpoints[activeProtocol] ? activeProtocol : Object.keys(selected?.endpoints ?? {})[0] as ProviderProtocol | undefined;

  async function load(preferredId?: string) {
    const value = await fetchProviderCatalog();
    setCatalog(value);
    setSelectedId((current) => value.providers.find((p) => p.id === (preferredId ?? current))?.id ?? value.providers[0]?.id ?? null);
    onConfiguredChange?.(value.providers.some((provider) => enabledCount(provider) > 0 && hasSelectedKey(provider)));
  }
  useEffect(() => { void load().catch((reason) => setMessage(errorText(reason))); }, []);
  useEffect(() => { setEditingEndpoints(false); setNewKey(""); setNewKeyLabel(""); }, [selectedId]);

  async function perform(action: () => Promise<unknown>, success: string) {
    setBusy(true); setMessage("");
    try { await action(); await load(); setMessage(success); }
    catch (reason) { await load().catch(() => undefined); setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  function choosePreset(presetId: string) {
    const preset = catalog?.presets.find((item) => item.id === presetId);
    setDraft((current) => ({ ...current, preset_id: presetId, name: preset?.name ?? current.name,
      endpoints: preset ? structuredClone(preset.endpoints) : current.endpoints }));
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!Object.keys(draft.endpoints).length) { setMessage("至少填写一个协议端点"); return; }
    setBusy(true); setMessage("");
    try {
      const result = await createModelProvider(draft);
      setDraft(EMPTY_PROVIDER); setAdding(false); await load(result.provider.id);
      setMessage(`已添加供应商 ${result.provider.name}。${result.warnings.length ? `部分端点读取失败：${result.warnings.join("；")}` : "模型已读取，请开启需要使用的模型。"}`);
    } catch (reason) { await load().catch(() => undefined); setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function saveEndpoints(event: FormEvent) {
    event.preventDefault(); if (!selected) return;
    if (!Object.keys(endpoints).length) { setMessage("至少填写一个协议端点"); return; }
    await perform(async () => { await updateProviderEndpoints(selected.id, endpoints); setEditingEndpoints(false); }, "协议端点已保存，可分别读取模型。");
  }

  async function appendKey(event: FormEvent) {
    event.preventDefault(); if (!selected) return;
    await perform(async () => {
      const result = await addProviderAPIKey(selected.id, { api_key: newKey, label: newKeyLabel || undefined });
      setNewKey(""); setNewKeyLabel("");
      if (result.warnings.length) throw new Error(`密钥已保存，部分端点读取失败：${result.warnings.join("；")}`);
    }, "密钥已保存并选中，各端点的模型已刷新。");
  }

  return <div className="ref-page models-catalog-page">
    <header className="ref-page-head models-page-head"><div><h1>模型供应商</h1><p>为供应商配置协议端点，并开启供 Solver 使用的模型。</p></div><button className="ref-primary-button" disabled={busy} onClick={() => setAdding(true)}><Plus size={16} />添加供应商</button></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    {adding ? <section className="provider-create-card" aria-label="添加供应商">
      <header><div><h2>添加供应商</h2><p>至少填写一个协议端点；不同端点共用所选 API 密钥。</p></div><button className="icon-button" disabled={busy} aria-label="关闭" onClick={() => setAdding(false)}><X size={18} /></button></header>
      <form onSubmit={create}>
        <label>供应商类型<select aria-label="供应商类型" disabled={busy} value={draft.preset_id} onChange={(event) => choosePreset(event.target.value)}><option value="custom">自定义供应商</option>{catalog?.presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label>
        <label>供应商名称<input required aria-label="供应商名称" disabled={busy} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如：团队网关" /></label>
        <EndpointFields endpoints={draft.endpoints} disabled={busy} onChange={(value) => setDraft({ ...draft, endpoints: value })} />
        <label className="wide">API 密钥<input required type="password" aria-label="API 密钥" disabled={busy} autoComplete="new-password" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })} /></label>
        <footer><button type="button" className="ref-secondary-button" disabled={busy} onClick={() => setAdding(false)}>取消</button><button className="ref-primary-button" disabled={busy}>{busy ? "正在保存…" : "保存供应商"}</button></footer>
      </form>
    </section> : null}
    <div className="provider-layout">
      <section className="provider-list" aria-label="已配置供应商">
        <header><div><h2>已配置</h2><span>{catalog?.providers.length ?? 0}</span></div><p>选择供应商管理端点与模型</p></header>
        {catalog?.providers.length ? catalog.providers.map((provider) => <button key={provider.id} disabled={busy} className={provider.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(provider.id)}>
          <span className="provider-mark"><Server size={18} /></span><span><strong>{provider.name}</strong><small>{Object.keys(provider.endpoints).length} 个端点 · {enabledCount(provider)} 个已启用模型</small></span><em className={enabledCount(provider) && hasSelectedKey(provider) ? "ready" : "pending"}>{enabledCount(provider) && hasSelectedKey(provider) ? "已配置" : "待配置"}</em><ChevronRight size={16} />
        </button>) : <div className="provider-empty"><Server size={24} /><strong>还没有供应商</strong><p>添加供应商后即可配置 Solver 模型。</p></div>}
      </section>
      <section className="provider-detail" aria-label="供应商详情">
        {selected ? <>
          <header className="provider-detail-head"><div><h2>{selected.name}</h2><p>{Object.keys(selected.endpoints).length} 个协议端点，共用 {selected.api_keys.filter((key) => key.configured).length} 个已填写密钥</p></div><div className="provider-detail-actions">
            <button className="ref-secondary-button" disabled={busy} onClick={() => { setEndpoints(structuredClone(selected.endpoints)); setEditingEndpoints(!editingEndpoints); }}>编辑协议端点</button>
            {!selected.built_in ? <button className="provider-delete-button" disabled={busy} onClick={() => {
              if (window.confirm(`确认删除供应商“${selected.name}”？其端点、模型和密钥记录会一起删除。`)) void perform(() => deleteModelProvider(selected.id), "供应商已删除。");
            }}><Trash2 size={15} />删除</button> : null}
          </div></header>
          {editingEndpoints ? <form className="provider-endpoints-editor" aria-label="编辑协议端点" onSubmit={saveEndpoints}>
            <EndpointFields endpoints={endpoints} disabled={busy} onChange={setEndpoints} />
            <footer><button className="ref-secondary-button" type="button" disabled={busy} onClick={() => setEditingEndpoints(false)}>取消</button><button className="ref-primary-button" disabled={busy}>保存端点</button></footer>
          </form> : null}
          <div className="workbench-tabs" role="tablist" aria-label="供应商协议端点">{Object.keys(selected.endpoints).map(protocol => <button key={protocol} role="tab" aria-selected={protocolToShow === protocol} onClick={() => setActiveProtocol(protocol as ProviderProtocol)}>{protocolName(protocol as ProviderProtocol)}</button>)}</div>
          <div className="provider-endpoint-lists">{Object.entries(selected.endpoints).filter(([protocol]) => protocol === protocolToShow).map(([protocol, endpoint]) => <EndpointModels key={`${selected.id}:${protocol}`} protocol={protocol as ProviderProtocol} endpoint={endpoint} busy={busy}
            onRefresh={() => void perform(() => discoverProviderModels(selected.id, protocol as ProviderProtocol), "模型已读取；新增模型默认关闭，已有启用选择已保留。")}
            onEnabled={(ids, enabled) => void perform(() => setProviderModelsEnabled(selected.id, protocol as ProviderProtocol, ids, enabled), enabled ? "模型已启用，可在对应 Solver 中选择。" : "模型已关闭，后续任务无法选择该模型。")}
          />)}</div>
          <section className="provider-keys provider-shared-keys"><header><div><h3><KeyRound size={16} />共享 API 密钥</h3><p>所选密钥用于此供应商的所有协议端点。</p></div></header>
            <div className="provider-items key-items">{selected.api_keys.map((key) => <button type="button" key={key.id} className={key.selected && key.configured ? "selected" : ""} disabled={busy || !key.configured} onClick={() => {
              if (!key.selected) void perform(async () => {
                const result = await selectProviderAPIKey(selected.id, key.id);
                if (result.warnings.length) throw new Error(`密钥已切换，部分端点读取失败：${result.warnings.join("；")}`);
              }, "密钥已切换，各端点的模型已刷新。");
            }}><span className="item-icon"><KeyRound size={16} /></span><span><strong>{key.label}</strong><code>{key.masked}</code></span>{!key.configured ? <small>未填写</small> : key.selected ? <em><Check size={13} />当前使用</em> : <small>点击选中</small>}</button>)}</div>
            <form className="provider-key-form" onSubmit={appendKey}><input aria-label="密钥备注" disabled={busy} value={newKeyLabel} onChange={(event) => setNewKeyLabel(event.target.value)} placeholder="备注（可选）" /><input required type="password" aria-label="添加 API 密钥" disabled={busy} autoComplete="new-password" value={newKey} onChange={(event) => setNewKey(event.target.value)} placeholder="输入新的 API 密钥" /><button disabled={busy}><Plus size={14} />添加 API 密钥</button></form>
          </section>
        </> : <div className="provider-detail-empty"><Server size={28} /><h2>选择一个供应商</h2><p>在左侧选择供应商，或添加新的供应商。</p></div>}
      </section>
    </div>
  </div>;
}

function EndpointFields({ endpoints, disabled, onChange }: { endpoints: ProviderEndpoints; disabled: boolean; onChange: (value: ProviderEndpoints) => void }) {
  return <fieldset className="provider-endpoint-fields wide" disabled={disabled}><legend>协议端点（至少一项）</legend><p>填写 API 基础地址，保留网关路径和版本号；系统按填写的地址连接。</p>
    {PROTOCOLS.map((protocol) => <div className="provider-endpoint-field" key={protocol}>
      <label className="endpoint-checkbox"><input type="checkbox" checked={Boolean(endpoints[protocol])} onChange={(event) => {
        const next = { ...endpoints };
        if (event.target.checked) next[protocol] = { base_url: "", models: [] }; else delete next[protocol];
        onChange(next);
      }} />{protocolName(protocol)}</label>
      {endpoints[protocol] ? <input required type="url" aria-label={`${protocolName(protocol)} API 地址`} value={endpoints[protocol].base_url} placeholder={protocol === "anthropic" ? "https://api.example.com/apps/anthropic" : "https://api.example.com/compatible-mode/v1"} onChange={(event) => onChange({ ...endpoints, [protocol]: { ...endpoints[protocol], base_url: event.target.value } })} /> : <span className="endpoint-not-configured">未配置</span>}
    </div>)}
  </fieldset>;
}

function EndpointModels({ protocol, endpoint, busy, onRefresh, onEnabled }: { protocol: ProviderProtocol; endpoint: ProviderEndpoint; busy: boolean; onRefresh: () => void; onEnabled: (ids: string[], enabled: boolean) => void }) {
  const [expanded, setExpanded] = useState(true);
  const [search, setSearch] = useState("");
  const [onlyEnabled, setOnlyEnabled] = useState(false);
  const query = search.trim().toLocaleLowerCase();
  const visible = endpoint.models
    .filter((model) => `${model.name} ${model.id}`.toLocaleLowerCase().includes(query) && (!onlyEnabled || model.enabled && model.available))
    .sort((a, b) => Number(b.enabled && b.available) - Number(a.enabled && a.available));
  const enabled = endpoint.models.filter((model) => model.enabled && model.available).length;
  return <section className="endpoint-models" aria-label={`${protocolName(protocol)} 模型`}>
    <header><button type="button" className="endpoint-models-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      {expanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}<span><strong>{protocolName(protocol)}</strong><small>{endpoint.base_url}</small></span><em>{enabled} / {endpoint.models.length} 已启用</em>
    </button><button type="button" className="ref-secondary-button" disabled={busy} onClick={onRefresh}><RefreshCw size={14} />读取可访问模型</button></header>
    {expanded ? <div className="endpoint-models-body">
      <div className="endpoint-models-toolbar"><span>开启后可在对应 Solver 中选择</span><button disabled={busy || !endpoint.models.some((model) => model.available && !model.enabled)} onClick={() => onEnabled(endpoint.models.filter((model) => model.available).map((model) => model.id), true)}>全部开启</button><button disabled={busy || !enabled} onClick={() => onEnabled(endpoint.models.map((model) => model.id), false)}>全部关闭</button></div>
      <div className="provider-model-toolbar"><label className="endpoint-model-search"><Search size={16} /><input type="search" aria-label={`搜索 ${protocolName(protocol)} 模型`} placeholder="搜索模型…" value={search} onChange={(event) => setSearch(event.target.value)} /></label><label><input type="checkbox" checked={onlyEnabled} onChange={event => setOnlyEnabled(event.target.checked)} />仅看已启用</label></div>
      <div className="endpoint-model-rows">{visible.map((model) => <div key={model.id} className="endpoint-model-row" data-enabled={model.enabled && model.available}>
        <button type="button" role="switch" className="model-enable-switch" aria-label={`启用 ${model.name}`} aria-checked={model.enabled && model.available} disabled={busy || !model.available} onClick={() => onEnabled([model.id], !model.enabled)}><span /></button>
        <strong title={model.name}>{model.name}</strong>{!model.available ? <small>已不可访问</small> : <small>{model.enabled ? "已启用" : "未启用"}</small>}
      </div>)}</div>
      {!visible.length ? <p className="provider-model-empty">{endpoint.models.length ? "没有匹配的模型。" : "尚未读取模型，请点击“读取可访问模型”。"}</p> : null}
    </div> : null}
  </section>;
}

function errorText(reason: unknown) { return reason instanceof Error ? reason.message : "操作失败，请稍后重试"; }
function enabledCount(provider: ModelProvider) { return Object.values(provider.endpoints).reduce((count, endpoint) => count + endpoint.models.filter((model) => model.enabled && model.available).length, 0); }
function hasSelectedKey(provider: ModelProvider) { return provider.api_keys.some((key) => key.selected && key.configured); }
function protocolName(protocol: ProviderProtocol) { return ({ openai_responses: "OpenAI Responses", openai_chat_completions: "OpenAI Chat Completions", anthropic: "Anthropic Messages" } as const)[protocol]; }
