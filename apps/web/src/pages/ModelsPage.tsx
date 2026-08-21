import { FormEvent, useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, Cpu, KeyRound, Plus, RefreshCw, Server, ShieldCheck, X } from "lucide-react";
import {
  addProviderAPIKey, createModelProvider, discoverProviderModels, fetchProviderCatalog,
  selectProviderAPIKey, updateProviderEndpoint,
  type ModelProvider, type ProviderCatalog,
} from "../api/tasks";

type ProviderDraft = { preset_id: string; name: string; base_url: string; api_key: string };

const EMPTY_PROVIDER: ProviderDraft = { preset_id: "custom", name: "", base_url: "", api_key: "" };

export function ModelsPage({ onConfiguredChange }: { onConfiguredChange?: (configured: boolean) => void }) {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<ProviderDraft>(EMPTY_PROVIDER);
  const [endpoint, setEndpoint] = useState("");
  const [newKey, setNewKey] = useState("");
  const [newKeyLabel, setNewKeyLabel] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const load = async (preferredId?: string) => {
    const value = await fetchProviderCatalog();
    setCatalog(value);
    setSelectedId((current) => preferredId ?? current ?? value.providers[0]?.id ?? null);
    onConfiguredChange?.(value.providers.some((provider) => provider.models.length > 0 && provider.api_keys.length > 0));
  };

  useEffect(() => { void load().catch((reason: unknown) => setMessage(errorText(reason))); }, []);

  const selected = useMemo(
    () => catalog?.providers.find((provider) => provider.id === selectedId) ?? null,
    [catalog, selectedId],
  );
  useEffect(() => { setEndpoint(selected?.base_url ?? ""); }, [selected?.id, selected?.base_url]);

  const choosePreset = (presetId: string) => {
    const preset = catalog?.presets.find((item) => item.id === presetId);
    setDraft((current) => ({
      ...current, preset_id: presetId,
      name: preset ? preset.name : current.name,
      base_url: preset ? preset.base_url : current.base_url,
    }));
  };

  const create = async (event: FormEvent) => {
    event.preventDefault(); setBusy("create"); setMessage("");
    try {
      const result = await createModelProvider({ ...draft, preset_id: draft.preset_id });
      setDraft(EMPTY_PROVIDER); setAdding(false);
      await load(result.provider.id);
      setMessage(`已添加供应商 ${result.provider.name}，并读取到 ${result.provider.models.length} 个可访问模型。`);
    } catch (reason) { await load().catch(() => undefined); setMessage(errorText(reason)); }
    finally { setBusy(""); }
  };

  const syncModels = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!selected) return;
    setBusy("models"); setMessage("");
    try {
      if (endpoint.trim() !== selected.base_url) await updateProviderEndpoint(selected.id, endpoint.trim());
      else await discoverProviderModels(selected.id);
      await load(selected.id);
      setMessage("已使用当前 API URL 和所选密钥刷新可访问模型。");
    }
    catch (reason) { await load(selected.id).catch(() => undefined); setMessage(errorText(reason)); }
    finally { setBusy(""); }
  };

  const appendKey = async (event: FormEvent) => {
    event.preventDefault(); if (!selected) return;
    setBusy("key"); setMessage("");
    try {
      await addProviderAPIKey(selected.id, { api_key: newKey, label: newKeyLabel || undefined });
      setNewKey(""); setNewKeyLabel(""); await load(selected.id);
      setMessage("API 密钥已保存并选中，可访问模型也已自动刷新。");
    } catch (reason) { await load(selected.id).catch(() => undefined); setMessage(errorText(reason)); }
    finally { setBusy(""); }
  };

  const selectKey = async (provider: ModelProvider, keyId: string) => {
    if (provider.selected_api_key_id === keyId) return;
    setBusy(`key:${keyId}`); setMessage("");
    try { await selectProviderAPIKey(provider.id, keyId); await load(provider.id); setMessage("已切换密钥并自动刷新可访问模型。"); }
    catch (reason) { await load(provider.id).catch(() => undefined); setMessage(errorText(reason)); }
    finally { setBusy(""); }
  };

  return <div className="ref-page models-catalog-page">
    <header className="ref-page-head models-page-head">
      <div><span className="eyebrow">MODEL REGISTRY</span><h1>模型供应商</h1><p>配置 API URL 与密钥后，系统自动读取该密钥可访问的模型。</p></div>
      <button className="ref-primary-button" onClick={() => setAdding(true)}><Plus size={16} />添加供应商</button>
    </header>

    {message ? <p className="settings-message" role="status">{message}</p> : null}

    {adding ? <section className="provider-create-card" aria-label="添加供应商">
      <header><div><h2>添加供应商</h2><p>选择官方预设会自动填写 API URL；也可以使用任意兼容端点。</p></div><button className="icon-button" aria-label="关闭" onClick={() => setAdding(false)}><X size={18} /></button></header>
      <form onSubmit={create}>
        <label>供应商类型<select aria-label="供应商类型" value={draft.preset_id} onChange={(event) => choosePreset(event.target.value)}><option value="custom">自定义</option>{catalog?.presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label>
        <label>供应商名称<input required aria-label="供应商名称" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如：团队网关" /></label>
        <label className="wide">API URL<input required type="url" aria-label="API URL" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder="https://api.example.com/v1" /></label>
        <label>API 密钥<input required type="password" aria-label="API 密钥" autoComplete="new-password" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })} placeholder="仅写入，不会回显" /></label>
        <footer><button type="button" className="ref-secondary-button" onClick={() => setAdding(false)}>取消</button><button className="ref-primary-button" disabled={busy === "create"}>{busy === "create" ? "正在保存…" : "保存供应商"}</button></footer>
      </form>
    </section> : null}

    <div className="provider-layout">
      <section className="provider-list" aria-label="已配置供应商">
        <header><div><h2>已配置</h2><span>{catalog?.providers.length ?? 0}</span></div><p>选择供应商查看模型与密钥</p></header>
        {catalog?.providers.length ? catalog.providers.map((provider) => {
          return <button key={provider.id} className={provider.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(provider.id)}>
            <span className="provider-mark"><Server size={18} /></span><span><strong>{provider.name}</strong><small>{provider.models.length} 个模型 · {provider.api_keys.length} 个密钥</small></span>
            <em className={provider.models.length && provider.api_keys.length ? "ready" : "pending"}>{provider.models.length && provider.api_keys.length ? "已配置" : "未完整配置"}</em><ChevronRight size={16} />
          </button>;
        }) : <div className="provider-empty"><Server size={24} /><strong>还没有供应商</strong><p>添加一个供应商后即可配置 Agent 使用的模型。</p></div>}
      </section>

      <section className="provider-detail" aria-label="供应商详情">
        {selected ? <>
          <header className="provider-detail-head"><div><span>{selected.preset_id === "custom" ? "自定义供应商" : "官方预设"}</span><h2>{selected.name}</h2><code>{selected.base_url}</code></div><div className="provider-counts"><span><Cpu size={15} />{selected.models.length} 模型</span><span><KeyRound size={15} />{selected.api_keys.length} 密钥</span></div></header>
          <form className="provider-endpoint-form" onSubmit={syncModels}><label>API URL<input required type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label><button className="ref-secondary-button" disabled={busy === "models"}><RefreshCw size={15} />{busy === "models" ? "读取中…" : "读取可访问模型"}</button></form>

          <div className="provider-detail-grid">
            <section className="provider-models"><header><div><h3>可访问模型</h3><p>列表由当前 API URL 和所选 API 密钥自动读取，不再手动添加。</p></div><button type="button" className="icon-button" aria-label="刷新模型" disabled={busy === "models"} onClick={() => void syncModels()}><RefreshCw size={16} /></button></header>
              <div className="provider-items">{selected.models.map((model) => <article key={model.id}>
                <span className="item-icon"><Cpu size={16} /></span><div><strong>{model.name}</strong><small>{model.max_output_tokens} tokens · {model.reasoning_mode === "enabled" ? "推理模式" : "标准模式"}</small></div>
                <span className="verification-pill verified"><Check size={12} />已配置</span>
              </article>)}</div>
              {!selected.models.length ? <p className="provider-model-empty">尚未读取到模型，请检查 API URL、所选密钥及供应商协议。</p> : null}
            </section>

            <section className="provider-keys"><header><div><h3>API 密钥</h3><p>点击条目即可选中；页面列表仅显示密钥掩码。</p></div></header>
              <div className="provider-items key-items">{selected.api_keys.map((key) => <button type="button" key={key.id} className={key.selected ? "selected" : ""} onClick={() => void selectKey(selected, key.id)} disabled={busy === `key:${key.id}`}>
                <span className="item-icon"><KeyRound size={16} /></span><span><strong>{key.label}</strong><code>{key.masked}</code></span>{key.selected ? <em><Check size={13} />当前使用</em> : <small>点击选中</small>}
              </button>)}</div>
              <form className="provider-key-form" onSubmit={appendKey}><input aria-label="密钥备注" value={newKeyLabel} onChange={(event) => setNewKeyLabel(event.target.value)} placeholder="备注（可选）" /><input required type="password" aria-label="添加 API 密钥" autoComplete="new-password" value={newKey} onChange={(event) => setNewKey(event.target.value)} placeholder="输入新的 API 密钥" /><button disabled={busy === "key"}><Plus size={14} />添加 API 密钥</button></form>
            </section>
          </div>
          <footer className="provider-security-note"><ShieldCheck size={16} /><span>密钥随 models.json 保存；输入框使用密码模式，列表只显示掩码。请仅在可信主机部署并限制 config 目录权限。</span></footer>
        </> : <div className="provider-detail-empty"><Server size={28} /><h2>选择一个供应商</h2><p>在左侧查看已配置供应商，或先添加新的供应商。</p></div>}
      </section>
    </div>
  </div>;
}

function errorText(reason: unknown): string {
  return reason instanceof Error ? reason.message : "操作失败，请稍后重试";
}
