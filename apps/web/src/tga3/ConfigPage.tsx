import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, LoaderCircle, Plus, Save, Settings2, Trash2 } from "lucide-react";
import { useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { tga3Api } from "./api";
import type { ConfigBundle, EditableProviderConfig } from "./types";

type ConfigTab = "models" | "agents" | "scenes" | "runtime";

export function ConfigPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["tga3", "config"], queryFn: tga3Api.config });
  const [draft, setDraft] = useState<ConfigBundle | null>(null);
  const [runtimeText, setRuntimeText] = useState("");
  const [tab, setTab] = useState<ConfigTab>("models");
  const [showKeys, setShowKeys] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!query.data) return;
    setDraft(structuredClone(query.data));
    setRuntimeText(JSON.stringify(query.data.runtime, null, 2));
  }, [query.data]);

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const runtime = JSON.parse(runtimeText) as ConfigBundle["runtime"];
      const saved = await tga3Api.saveConfig({ ...draft, runtime });
      setDraft(structuredClone(saved));
      setRuntimeText(JSON.stringify(saved.runtime, null, 2));
      setNotice("配置已校验并原子写回 config 目录；新任务将立即使用。需要重启的监听和数据库字段已保存。 ");
      await Promise.all([
        client.invalidateQueries({ queryKey: ["tga3", "models"] }),
        client.invalidateQueries({ queryKey: ["tga3", "scenes"] }),
        client.invalidateQueries({ queryKey: ["tga3", "config"] }),
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存配置失败");
    } finally {
      setSaving(false);
    }
  };

  if (query.error) return <div className="error-box">{query.error.message}</div>;
  if (query.isLoading || !draft) return <div className="config-loading"><LoaderCircle className="spin" />正在读取 config</div>;

  return <section className="page-stack config-page">
    <header className="page-head"><div><small>CONFIG</small><h1>统一配置中心</h1><p>这里直接读写 Ubuntu 仓库的 config 文件，不在数据库维护第二份全局配置。</p></div><button className="primary-button" disabled={saving} onClick={() => void save()}>{saving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}{saving ? "校验并保存中" : "保存全部配置"}</button></header>
    <nav className="config-tabs">{([['models', '供应商与模型'], ['agents', 'Agents'], ['scenes', '场景'], ['runtime', 'Runtime']] as const).map(([id, label]) => <button className={tab === id ? "active" : ""} key={id} onClick={() => setTab(id)}>{label}</button>)}</nav>
    {notice ? <div className="config-notice">{notice}</div> : null}
    {error ? <div className="error-box">{error}</div> : null}
    {tab === "models" ? <ModelsEditor draft={draft} setDraft={setDraft} showKeys={showKeys} setShowKeys={setShowKeys} /> : null}
    {tab === "agents" ? <AgentsEditor draft={draft} setDraft={setDraft} /> : null}
    {tab === "scenes" ? <ScenesEditor draft={draft} setDraft={setDraft} /> : null}
    {tab === "runtime" ? <RuntimeEditor value={runtimeText} setValue={setRuntimeText} /> : null}
  </section>;
}

function ModelsEditor({ draft, setDraft, showKeys, setShowKeys }: {
  draft: ConfigBundle;
  setDraft: Dispatch<SetStateAction<ConfigBundle | null>>;
  showKeys: boolean;
  setShowKeys: (value: boolean) => void;
}) {
  const change = (index: number, mutate: (provider: EditableProviderConfig) => void) => setDraft((current) => {
    if (!current) return current;
    const next = structuredClone(current);
    mutate(next.models.providers[index]);
    return next;
  });
  const addProvider = () => setDraft((current) => {
    if (!current) return current;
    const next = structuredClone(current);
    const id = uniqueId("provider", next.models.providers.map((item) => item.id));
    next.models.providers.push({
      id,
      name: "新供应商",
      protocol: "openai_responses",
      base_url: "https://api.openai.com/v1",
      api_keys: [{ id: "primary", label: "Primary", api_key: "" }],
      selected_api_key_id: "primary",
      models: [{ id: "model-1", name: "新模型", max_output_tokens: 8192, timeout_seconds: 180 }],
    });
    return next;
  });
  const used = new Set(Object.values(draft.agents.agents).map((agent) => agent.provider_id));
  return <div className="config-section">
    <div className="config-section-head"><div><h2>供应商、模型与密钥</h2><p>API Key 的真实值来自 models.json；密码框只是视觉遮罩。</p></div><div><button className="outline-button" onClick={() => setShowKeys(!showKeys)}>{showKeys ? <EyeOff size={15} /> : <Eye size={15} />}{showKeys ? "隐藏密钥" : "显示密钥"}</button><button className="primary-button" onClick={addProvider}><Plus size={15} />添加供应商</button></div></div>
    <div className="provider-edit-list">{draft.models.providers.map((provider, providerIndex) => <article className="provider-editor" key={`${provider.id}-${providerIndex}`}>
      <header><div><Settings2 size={18} /><b>{provider.name || provider.id}</b></div><button className="icon-danger" title={used.has(provider.id) ? "先修改使用该供应商的 Agent" : "删除供应商"} disabled={used.has(provider.id)} onClick={() => setDraft((current) => {
        if (!current) return current;
        const next = structuredClone(current);
        next.models.providers.splice(providerIndex, 1);
        return next;
      })}><Trash2 size={15} /></button></header>
      <div className="config-form-grid">
        <Field label="供应商 ID"><input value={provider.id} onChange={(event) => setDraft((current) => {
          if (!current) return current;
          const next = structuredClone(current);
          const old = next.models.providers[providerIndex].id;
          next.models.providers[providerIndex].id = event.target.value;
          Object.values(next.agents.agents).forEach((agent) => { if (agent.provider_id === old) agent.provider_id = event.target.value; });
          return next;
        })} /></Field>
        <Field label="显示名称"><input value={provider.name} onChange={(event) => change(providerIndex, (item) => { item.name = event.target.value; })} /></Field>
        <Field label="协议"><select value={provider.protocol} disabled={used.has(provider.id)} title={used.has(provider.id) ? "被 Agent 使用的供应商不能直接改变协议" : ""} onChange={(event) => change(providerIndex, (item) => { item.protocol = event.target.value as EditableProviderConfig["protocol"]; })}><option value="openai_responses">OpenAI Responses</option><option value="openai_chat_completions">OpenAI Chat Completions</option><option value="anthropic">Anthropic</option></select></Field>
        <Field label="Base URL"><input value={provider.base_url ?? ""} onChange={(event) => change(providerIndex, (item) => { item.base_url = event.target.value || null; })} /></Field>
      </div>
      <section className="key-editor"><header><b>API Keys</b><button onClick={() => change(providerIndex, (item) => {
        const id = uniqueId("key", item.api_keys.map((key) => key.id));
        item.api_keys.push({ id, label: "New Key", api_key: "" });
        item.selected_api_key_id = id;
      })}><Plus size={13} />添加 Key</button></header>{provider.api_keys.map((key, keyIndex) => <div key={`${key.id}-${keyIndex}`}>
        <input aria-label="Key ID" value={key.id} onChange={(event) => change(providerIndex, (item) => {
          const old = item.api_keys[keyIndex].id;
          item.api_keys[keyIndex].id = event.target.value;
          if (item.selected_api_key_id === old) item.selected_api_key_id = event.target.value;
        })} />
        <input aria-label="Key 标签" value={key.label} onChange={(event) => change(providerIndex, (item) => { item.api_keys[keyIndex].label = event.target.value; })} />
        <input aria-label="API Key" type={showKeys ? "text" : "password"} value={key.api_key} onChange={(event) => change(providerIndex, (item) => { item.api_keys[keyIndex].api_key = event.target.value; })} />
        <label><input type="radio" name={`selected-key-${providerIndex}`} checked={provider.selected_api_key_id === key.id} onChange={() => change(providerIndex, (item) => { item.selected_api_key_id = key.id; })} />启用</label>
        <button className="icon-danger" disabled={provider.api_keys.length === 1} onClick={() => change(providerIndex, (item) => {
          item.api_keys.splice(keyIndex, 1);
          if (!item.api_keys.some((value) => value.id === item.selected_api_key_id)) item.selected_api_key_id = item.api_keys[0].id;
        })}><Trash2 size={13} /></button>
      </div>)}</section>
      <section className="model-editor"><header><b>模型</b><button onClick={() => change(providerIndex, (item) => item.models.push({ id: uniqueId("model", item.models.map((model) => model.id)), name: "新模型", max_output_tokens: 8192, timeout_seconds: 180 }))}><Plus size={13} />添加模型</button></header>{provider.models.map((model, modelIndex) => <div key={`${model.id}-${modelIndex}`}>
        <input aria-label="模型 ID" value={model.id} onChange={(event) => setDraft((current) => {
          if (!current) return current;
          const next = structuredClone(current);
          const targetProvider = next.models.providers[providerIndex];
          const old = targetProvider.models[modelIndex].id;
          targetProvider.models[modelIndex].id = event.target.value;
          Object.values(next.agents.agents).forEach((agent) => {
            if (agent.provider_id === targetProvider.id && agent.model_id === old) agent.model_id = event.target.value;
          });
          return next;
        })} />
        <input aria-label="模型名称" value={model.name} onChange={(event) => change(providerIndex, (item) => { item.models[modelIndex].name = event.target.value; })} />
        <label>输出 Token<input type="number" min={1} value={model.max_output_tokens} onChange={(event) => change(providerIndex, (item) => { item.models[modelIndex].max_output_tokens = Number(event.target.value); })} /></label>
        <label>超时秒数<input type="number" min={1} value={model.timeout_seconds} onChange={(event) => change(providerIndex, (item) => { item.models[modelIndex].timeout_seconds = Number(event.target.value); })} /></label>
        <label>Temperature<input type="number" min={0} max={2} step={0.1} value={typeof model.temperature === "number" ? model.temperature : ""} placeholder="默认" onChange={(event) => change(providerIndex, (item) => { item.models[modelIndex].temperature = event.target.value === "" ? null : Number(event.target.value); })} /></label>
        <button className="icon-danger" disabled={provider.models.length === 1 || Object.values(draft.agents.agents).some((agent) => agent.provider_id === provider.id && agent.model_id === model.id)} onClick={() => change(providerIndex, (item) => { item.models.splice(modelIndex, 1); })}><Trash2 size={13} /></button>
      </div>)}</section>
    </article>)}</div>
  </div>;
}

function AgentsEditor({ draft, setDraft }: { draft: ConfigBundle; setDraft: Dispatch<SetStateAction<ConfigBundle | null>> }) {
  const change = (agentId: string, mutate: (agent: ConfigBundle["agents"]["agents"][string]) => void) => setDraft((current) => {
    if (!current) return current;
    const next = structuredClone(current);
    mutate(next.agents.agents[agentId]);
    return next;
  });
  return <div className="config-section"><div className="config-section-head"><div><h2>Agent 配置</h2><p>身份、默认模型、周期轮数和完整系统提示词都写入 agents.json。</p></div></div><div className="agent-edit-list">{Object.entries(draft.agents.agents).map(([agentId, agent]) => {
    const providers = draft.models.providers.filter((provider) => agent.runtime === "claude_agent" ? provider.protocol === "anthropic" : provider.protocol !== "anthropic");
    const provider = providers.find((item) => item.id === agent.provider_id);
    return <article className="agent-editor" key={agentId}><header><div><b>{agent.display_name}</b><code>{agentId}</code></div><span>{agent.role} · {agent.runtime}</span></header><div className="config-form-grid">
      <Field label="显示名称"><input value={agent.display_name} onChange={(event) => change(agentId, (item) => { item.display_name = event.target.value; })} /></Field>
      <Field label="供应商"><select value={agent.provider_id} onChange={(event) => change(agentId, (item) => {
        item.provider_id = event.target.value;
        item.model_id = draft.models.providers.find((value) => value.id === event.target.value)?.models[0]?.id ?? "";
      })}>{providers.map((item) => <option value={item.id} key={item.id}>{item.name} ({item.id})</option>)}</select></Field>
      <Field label="模型"><select value={agent.model_id} onChange={(event) => change(agentId, (item) => { item.model_id = event.target.value; })}>{provider?.models.map((model) => <option value={model.id} key={model.id}>{model.name} ({model.id})</option>)}</select></Field>
      <Field label="每周期模型轮数"><input type="number" min={1} max={50} value={agent.max_turns_per_cycle} onChange={(event) => change(agentId, (item) => { item.max_turns_per_cycle = Number(event.target.value); })} /></Field>
    </div><Field label="System Prompt"><textarea rows={8} value={agent.system_prompt} onChange={(event) => change(agentId, (item) => { item.system_prompt = event.target.value; })} /></Field></article>;
  })}</div></div>;
}

function ScenesEditor({ draft, setDraft }: { draft: ConfigBundle; setDraft: Dispatch<SetStateAction<ConfigBundle | null>> }) {
  const change = (index: number, field: "name" | "description" | "system_prompt", value: string) => setDraft((current) => {
    if (!current) return current;
    const next = structuredClone(current);
    next.scenes.scenes[index][field] = value;
    return next;
  });
  return <div className="config-section"><div className="config-section-head"><div><h2>场景提示词</h2><p>新任务会把所选场景的 system_prompt 作为第一条黑板提示。</p></div></div><div className="scene-edit-list">{draft.scenes.scenes.map((scene, index) => <article className="scene-editor" key={scene.id}><header><b>{scene.name}</b><code>{scene.id}</code></header><Field label="场景名称"><input value={scene.name} onChange={(event) => change(index, "name", event.target.value)} /></Field><Field label="场景说明"><input value={scene.description} onChange={(event) => change(index, "description", event.target.value)} /></Field><Field label="场景 System Prompt"><textarea rows={7} value={scene.system_prompt} onChange={(event) => change(index, "system_prompt", event.target.value)} /></Field></article>)}</div></div>;
}

function RuntimeEditor({ value, setValue }: { value: string; setValue: (value: string) => void }) {
  return <div className="config-section"><div className="config-section-head"><div><h2>Runtime 配置</h2><p>容器、目录和周期参数保存后对新任务生效；监听地址和 PostgreSQL DSN 需要重启主服务。</p></div></div><textarea className="runtime-json-editor" spellCheck={false} value={value} onChange={(event) => setValue(event.target.value)} /></div>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="config-field"><span>{label}</span>{children}</label>;
}

function uniqueId(prefix: string, existing: string[]) {
  let index = 1;
  while (existing.includes(`${prefix}-${index}`)) index += 1;
  return `${prefix}-${index}`;
}
