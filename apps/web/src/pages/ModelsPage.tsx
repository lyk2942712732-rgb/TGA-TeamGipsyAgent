import { FormEvent, useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, Cpu, KeyRound, Plus, Server, ShieldCheck, X } from "lucide-react";
import {
  addProviderAPIKey, addProviderModel, createModelProvider, fetchProviderCatalog,
  selectProviderAPIKey, verifyProviderModel,
  type ModelProvider, type ProviderCatalog,
} from "../api/tasks";

type ProviderDraft = { preset_id: string; name: string; base_url: string; model: string; api_key: string };

const EMPTY_PROVIDER: ProviderDraft = { preset_id: "custom", name: "", base_url: "", model: "", api_key: "" };

export function ModelsPage({ onConfiguredChange }: { onConfiguredChange?: (configured: boolean) => void }) {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<ProviderDraft>(EMPTY_PROVIDER);
  const [newModel, setNewModel] = useState("");
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
      setMessage(`已添加供应商 ${result.provider.name}，请验证模型连接后用于任务。`);
    } catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(""); }
  };

  const appendModel = async (event: FormEvent) => {
    event.preventDefault(); if (!selected) return;
    setBusy("model"); setMessage("");
    try { await addProviderModel(selected.id, { name: newModel }); setNewModel(""); await load(selected.id); }
    catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(""); }
  };

  const appendKey = async (event: FormEvent) => {
    event.preventDefault(); if (!selected) return;
    setBusy("key"); setMessage("");
    try {
      await addProviderAPIKey(selected.id, { api_key: newKey, label: newKeyLabel || undefined });
      setNewKey(""); setNewKeyLabel(""); await load(selected.id);
      setMessage("API 密钥已保存并选中。切换密钥后需要重新验证模型。");
    } catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(""); }
  };

  const selectKey = async (provider: ModelProvider, keyId: string) => {
    if (provider.selected_api_key_id === keyId) return;
    setBusy(`key:${keyId}`); setMessage("");
    try { await selectProviderAPIKey(provider.id, keyId); await load(provider.id); }
    catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(""); }
  };

  const verify = async (providerId: string, modelId: string) => {
    setBusy(`verify:${modelId}`); setMessage("");
    try {
      await verifyProviderModel(providerId, modelId); await load(providerId);
      setMessage("连接与工具调用协议验证通过，该模型现在可分配给 Agent。");
    } catch (reason) { setMessage(errorText(reason)); await load(providerId).catch(() => undefined); }
    finally { setBusy(""); }
  };

  return <div className="ref-page models-catalog-page">
    <header className="ref-page-head models-page-head">
      <div><span className="eyebrow">MODEL REGISTRY</span><h1>模型供应商</h1><p>集中管理 OpenAI-compatible 供应商、模型和任务使用的 API 密钥。</p></div>
      <button className="ref-primary-button" onClick={() => setAdding(true)}><Plus size={16} />添加供应商</button>
    </header>

    {message ? <p className="settings-message" role="status">{message}</p> : null}

    {adding ? <section className="provider-create-card" aria-label="添加供应商">
      <header><div><h2>添加供应商</h2><p>选择官方预设会自动填写 API URL；也可以使用任意兼容端点。</p></div><button className="icon-button" aria-label="关闭" onClick={() => setAdding(false)}><X size={18} /></button></header>
      <form onSubmit={create}>
        <label>供应商类型<select aria-label="供应商类型" value={draft.preset_id} onChange={(event) => choosePreset(event.target.value)}><option value="custom">自定义</option>{catalog?.presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label>
        <label>供应商名称<input required aria-label="供应商名称" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如：团队网关" /></label>
        <label className="wide">API URL<input required type="url" aria-label="API URL" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder="https://api.example.com/v1" /></label>
        <label>模型名称<input required aria-label="模型名称" value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} placeholder="例如：gpt-5" /></label>
        <label>API 密钥<input required type="password" aria-label="API 密钥" autoComplete="new-password" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })} placeholder="仅写入，不会回显" /></label>
        <footer><button type="button" className="ref-secondary-button" onClick={() => setAdding(false)}>取消</button><button className="ref-primary-button" disabled={busy === "create"}>{busy === "create" ? "正在保存…" : "保存供应商"}</button></footer>
      </form>
    </section> : null}

    <div className="provider-layout">
      <section className="provider-list" aria-label="已配置供应商">
        <header><div><h2>已配置</h2><span>{catalog?.providers.length ?? 0}</span></div><p>选择供应商查看模型与密钥</p></header>
        {catalog?.providers.length ? catalog.providers.map((provider) => {
          const verified = provider.models.filter((model) => model.verification_status === "verified").length;
          return <button key={provider.id} className={provider.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(provider.id)}>
            <span className="provider-mark"><Server size={18} /></span><span><strong>{provider.name}</strong><small>{provider.models.length} 个模型 · {provider.api_keys.length} 个密钥</small></span>
            <em className={verified ? "ready" : "pending"}>{verified ? `${verified} 可用` : "待验证"}</em><ChevronRight size={16} />
          </button>;
        }) : <div className="provider-empty"><Server size={24} /><strong>还没有供应商</strong><p>添加一个供应商后即可配置 Agent 使用的模型。</p></div>}
      </section>

      <section className="provider-detail" aria-label="供应商详情">
        {selected ? <>
          <header className="provider-detail-head"><div><span>{selected.preset_id === "custom" ? "自定义供应商" : "官方预设"}</span><h2>{selected.name}</h2><code>{selected.base_url}</code></div><div className="provider-counts"><span><Cpu size={15} />{selected.models.length} 模型</span><span><KeyRound size={15} />{selected.api_keys.length} 密钥</span></div></header>

          <div className="provider-detail-grid">
            <section className="provider-models"><header><div><h3>模型</h3><p>验证通过后可在任务开始前分配给 Agent。</p></div></header>
              <div className="provider-items">{selected.models.map((model) => <article key={model.id}>
                <span className="item-icon"><Cpu size={16} /></span><div><strong>{model.name}</strong><small>{model.max_output_tokens} tokens · {model.reasoning_mode === "enabled" ? "推理模式" : "标准模式"}</small></div>
                <span className={`verification-pill ${model.verification_status}`}>{model.verification_status === "verified" ? <><Check size={12} />已验证</> : model.verification_status === "failed" ? "验证失败" : model.verification_status === "stale" ? "需重新验证" : "未验证"}</span>
                <button className="ref-secondary-button" disabled={busy === `verify:${model.id}`} onClick={() => void verify(selected.id, model.id)}>{busy === `verify:${model.id}` ? "验证中…" : "验证"}</button>
              </article>)}</div>
              <form className="provider-inline-form" onSubmit={appendModel}><input required aria-label="添加模型" value={newModel} onChange={(event) => setNewModel(event.target.value)} placeholder="输入模型名称" /><button disabled={busy === "model"}><Plus size={14} />添加模型</button></form>
            </section>

            <section className="provider-keys"><header><div><h3>API 密钥</h3><p>点击条目即可选中；完整密钥不会从服务端回显。</p></div></header>
              <div className="provider-items key-items">{selected.api_keys.map((key) => <button type="button" key={key.id} className={key.selected ? "selected" : ""} onClick={() => void selectKey(selected, key.id)} disabled={busy === `key:${key.id}`}>
                <span className="item-icon"><KeyRound size={16} /></span><span><strong>{key.label}</strong><code>{key.masked}</code></span>{key.selected ? <em><Check size={13} />当前使用</em> : <small>点击选中</small>}
              </button>)}</div>
              <form className="provider-key-form" onSubmit={appendKey}><input aria-label="密钥备注" value={newKeyLabel} onChange={(event) => setNewKeyLabel(event.target.value)} placeholder="备注（可选）" /><input required type="password" aria-label="添加 API 密钥" autoComplete="new-password" value={newKey} onChange={(event) => setNewKey(event.target.value)} placeholder="输入新的 API 密钥" /><button disabled={busy === "key"}><Plus size={14} />添加 API 密钥</button></form>
            </section>
          </div>
          <footer className="provider-security-note"><ShieldCheck size={16} /><span>密钥以受限权限保存在本机（Windows 使用 DPAPI 保护）；页面和 API 只展示掩码。更换密钥会使模型验证失效，避免任务使用未经确认的凭据。</span></footer>
        </> : <div className="provider-detail-empty"><Server size={28} /><h2>选择一个供应商</h2><p>在左侧查看已配置供应商，或先添加新的供应商。</p></div>}
      </section>
    </div>
  </div>;
}

function errorText(reason: unknown): string {
  return reason instanceof Error ? reason.message : "操作失败，请稍后重试";
}
