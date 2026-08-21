import { FormEvent, useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, Cpu, KeyRound, Plus, RefreshCw, Server, ShieldCheck, Trash2, X } from "lucide-react";
import {
  addProviderAPIKey, createModelProvider, deleteModelProvider, discoverProviderModels, fetchProviderCatalog,
  selectProviderAPIKey, updateProviderEndpoint,
  type ModelProvider, type ProviderCatalog,
} from "../api/tasks";

type ProviderProtocol = "openai_responses" | "openai_chat_completions" | "anthropic";
type ProviderDraft = { preset_id: string; name: string; protocol: ProviderProtocol; base_url: string; api_key: string };

const EMPTY_PROVIDER: ProviderDraft = {
  preset_id: "custom", name: "", protocol: "openai_chat_completions", base_url: "", api_key: "",
};

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
    onConfiguredChange?.(value.providers.some((provider) => provider.models.length > 0 && configuredKeyCount(provider) > 0));
  };

  useEffect(() => { void load().catch((reason: unknown) => setMessage(errorText(reason))); }, []);

  const selected = useMemo(
    () => catalog?.providers.find((provider) => provider.id === selectedId) ?? null,
    [catalog, selectedId],
  );
  const availablePresets = useMemo(
    () => catalog?.presets.filter(
      (preset) => !catalog.providers.some((provider) => provider.preset_id === preset.id),
    ) ?? [],
    [catalog],
  );
  useEffect(() => { setEndpoint(selected?.base_url ?? ""); }, [selected?.id, selected?.base_url]);

  const choosePreset = (presetId: string) => {
    const preset = catalog?.presets.find((item) => item.id === presetId);
    setDraft((current) => ({
      ...current, preset_id: presetId,
      name: preset ? preset.name : current.name,
      protocol: preset ? preset.protocol : current.protocol,
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

  const removeProvider = async () => {
    if (!selected || selected.built_in) return;
    if (!window.confirm(`确认删除供应商“${selected.name}”？其密钥和模型记录会一起删除。`)) return;
    setBusy("delete"); setMessage("");
    try {
      await deleteModelProvider(selected.id);
      setSelectedId(null);
      await load();
      setMessage(`已删除供应商 ${selected.name}。`);
    } catch (reason) { await load(selected.id).catch(() => undefined); setMessage(errorText(reason)); }
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
      <div><span className="eyebrow">MODEL REGISTRY</span><h1>模型供应商</h1><p>只填写 API 根地址与密钥，系统会按供应商自动补全协议路径并读取可访问模型。</p></div>
      <button className="ref-primary-button" onClick={() => setAdding(true)}><Plus size={16} />添加供应商</button>
    </header>

    {message ? <p className="settings-message" role="status">{message}</p> : null}

    {adding ? <section className="provider-create-card" aria-label="添加供应商">
      <header><div><h2>添加供应商</h2><p>选择官方预设会自动填写 API URL；也可以使用任意兼容端点。</p></div><button className="icon-button" aria-label="关闭" onClick={() => setAdding(false)}><X size={18} /></button></header>
      <form onSubmit={create}>
        <label>供应商类型<select aria-label="供应商类型" value={draft.preset_id} onChange={(event) => choosePreset(event.target.value)}><option value="custom">自定义供应商</option>{availablePresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label>
        <label>供应商名称<input required aria-label="供应商名称" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如：团队网关" /></label>
        {draft.preset_id === "custom" ? <label>兼容协议<select aria-label="兼容协议" value={draft.protocol} onChange={(event) => setDraft({ ...draft, protocol: event.target.value as ProviderProtocol })}><option value="openai_chat_completions">OpenAI Chat Completions</option><option value="openai_responses">OpenAI Responses</option><option value="anthropic">Anthropic Messages</option></select></label> : null}
        <label className="wide">API 根地址<input required type="url" aria-label="API URL" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder="https://api.example.com" /><small>只需填写协议、域名和端口，不需要填写 /v1、/anthropic 或 /models。</small></label>
        <label>API 密钥<input required type="password" aria-label="API 密钥" autoComplete="new-password" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })} placeholder="仅写入，不会回显" /></label>
        <footer><button type="button" className="ref-secondary-button" onClick={() => setAdding(false)}>取消</button><button className="ref-primary-button" disabled={busy === "create"}>{busy === "create" ? "正在保存…" : "保存供应商"}</button></footer>
      </form>
    </section> : null}

    <div className="provider-layout">
      <section className="provider-list" aria-label="已配置供应商">
        <header><div><h2>已配置</h2><span>{catalog?.providers.length ?? 0}</span></div><p>选择供应商查看模型与密钥</p></header>
        {catalog?.providers.length ? catalog.providers.map((provider) => {
          return <button key={provider.id} className={provider.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(provider.id)}>
            <span className="provider-mark"><Server size={18} /></span><span><strong>{provider.name}</strong><small>{provider.models.length} 个模型 · {configuredKeyCount(provider)} 个有效密钥</small></span>
            <em className={provider.models.length && configuredKeyCount(provider) ? "ready" : "pending"}>{provider.models.length && configuredKeyCount(provider) ? "已配置" : "未完整配置"}</em><ChevronRight size={16} />
          </button>;
        }) : <div className="provider-empty"><Server size={24} /><strong>还没有供应商</strong><p>添加一个供应商后即可配置 Agent 使用的模型。</p></div>}
      </section>

      <section className="provider-detail" aria-label="供应商详情">
        {selected ? <>
          <header className="provider-detail-head"><div><span>{selected.preset_id === "custom" ? "自定义供应商" : "供应商预设"}</span><h2>{selected.name}</h2><code>{selected.base_url}</code></div><div className="provider-detail-actions"><div className="provider-counts"><span><Cpu size={15} />{selected.models.length} 模型</span><span><KeyRound size={15} />{configuredKeyCount(selected)} 有效密钥</span></div>{!selected.built_in ? <button type="button" className="provider-delete-button" disabled={busy === "delete"} onClick={() => void removeProvider()}><Trash2 size={15} />删除</button> : null}</div></header>
          <form className="provider-endpoint-form" onSubmit={syncModels}><label>API 根地址<input required type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /><small>系统会自动拼接模型发现与 SDK 推理路径。</small></label><button className="ref-secondary-button" disabled={busy === "models"}><RefreshCw size={15} />{busy === "models" ? "读取中…" : "读取可访问模型"}</button></form>

          <div className="provider-detail-grid">
            <section className="provider-models"><header><div><h3>可访问模型</h3><p>列表由当前 API URL 和所选 API 密钥自动读取，不再手动添加。</p></div><button type="button" className="icon-button" aria-label="刷新模型" disabled={busy === "models"} onClick={() => void syncModels()}><RefreshCw size={16} /></button></header>
              <div className="provider-items">{selected.models.map((model) => <article key={model.id}>
                <span className="item-icon"><Cpu size={16} /></span><div><strong>{model.name}</strong><small>{model.max_output_tokens} tokens · {model.reasoning_mode === "enabled" ? "推理模式" : "标准模式"}</small></div>
                <span className={`verification-pill ${model.verification_status === "verified" ? "verified" : "stale"}`}><Check size={12} />{model.verification_status === "verified" ? "已同步" : "待同步"}</span>
              </article>)}</div>
              {!selected.models.length ? <p className="provider-model-empty">尚未读取到模型，请检查 API URL、所选密钥及供应商协议。</p> : null}
            </section>

            <section className="provider-keys"><header><div><h3>API 密钥</h3><p>点击条目即可选中；页面列表仅显示密钥掩码。</p></div></header>
              <div className="provider-items key-items">{selected.api_keys.map((key) => <button type="button" key={key.id} className={key.selected && key.configured !== false ? "selected" : ""} onClick={() => void selectKey(selected, key.id)} disabled={busy === `key:${key.id}` || key.configured === false}>
                <span className="item-icon"><KeyRound size={16} /></span><span><strong>{key.label}</strong><code>{key.masked}</code></span>{key.configured === false ? <small>请在下方添加密钥</small> : key.selected ? <em><Check size={13} />当前使用</em> : <small>点击选中</small>}
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

function configuredKeyCount(provider: ModelProvider): number {
  return provider.api_keys.filter((key) => key.configured !== false).length;
}
