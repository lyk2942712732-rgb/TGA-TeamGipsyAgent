import { type ChangeEvent, type ClipboardEvent, type DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { Check, FileText, Image, Paperclip, Play, X } from "lucide-react";
import {
  createTask, deleteStagedInput, fetchAgentModelOptions, fetchModeProfiles, stageInput,
  type AgentModelOptions, type CreateTaskRequest, type ModeProfileContract, type StagedAsset,
} from "../api/tasks";
import { TASK_MODES, type TaskMode } from "../modes";

export function newTaskId(): string {
  const uuid = globalThis.crypto?.randomUUID;
  if (typeof uuid === "function") return `task_${uuid.call(globalThis.crypto).replace(/-/g, "").slice(0, 12)}`;
  return `task_${`${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`.slice(0, 12).padEnd(12, "0")}`;
}

const SCENE_ICONS: Record<TaskMode, string> = {
  penetration_test: "WEB", incident_response: "IR", vulnerability_research: "VR", reverse_engineering: "RE",
  pwn: "PWN", security_misc: "MISC", cryptography: "CRYPTO", forensics: "FORENSICS",
};

const emptyPolicy: CreateTaskRequest["executionPolicy"] = {
  preset: "autonomous_ctf",
  network: {
    access: "public_internet", interaction: "interact", seed_origins: [], custom_origins: [], custom_domains: [], custom_cidrs: [],
    deny_private_networks: false, deny_loopback: false, deny_link_local: false, deny_cloud_metadata: true,
    rate_limit_per_minute: 120, concurrency: 8, request_timeout_seconds: 60,
  },
  local_compute: { mode: "isolated", timeout_seconds: 900, concurrency: 2, network_inheritance: "task_network_policy" },
  high_impact: { mode: "approval_required", allowed_actions: [] },
};

export function NewTaskPage({ onCreated, onCancel = () => history.back() }: { onCreated: (id: string) => void; onCancel?: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sceneId, setSceneId] = useState<TaskMode>("penetration_test");
  const [profiles, setProfiles] = useState<ModeProfileContract[]>([]);
  const [assets, setAssets] = useState<StagedAsset[]>([]);
  const [agentOptions, setAgentOptions] = useState<AgentModelOptions | null>(null);
  const [agentModels, setAgentModels] = useState<Record<string, { provider_id: string; model_id: string }>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const assetsRef = useRef<StagedAsset[]>([]);

  useEffect(() => { void fetchModeProfiles().then((value) => setProfiles(value.profiles)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取场景")); }, []);
  useEffect(() => {
    void fetchAgentModelOptions(sceneId).then((value) => {
      setAgentOptions(value);
      setAgentModels(Object.fromEntries(value.agents.map((agent) => [agent.id, { provider_id: agent.model.provider_id, model_id: agent.model.model_id }])));
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取 Agent 模型"));
  }, [sceneId]);
  assetsRef.current = assets;
  useEffect(() => () => assetsRef.current.forEach((asset) => { if (asset.previewUrl) URL.revokeObjectURL(asset.previewUrl); }), []);

  const selected = useMemo(() => profiles.find((profile) => profile.id === sceneId), [profiles, sceneId]);
  const ready = Boolean(name.trim() && description.trim() && profiles.length && agentOptions && agentOptions.agents.every((agent) => {
    const selectedModel = agentModels[agent.id];
    return selectedModel && agentOptions.models.some((model) => model.provider_id === selectedModel.provider_id && model.model_id === selectedModel.model_id && model.ready && modelCompatible(agent.runtime, model.protocol));
  }) && !busy && assets.every((asset) => asset.status === "uploaded"));

  async function upload(files: File[]) {
    if (!files.length) return;
    setBusy(true); setError("");
    try {
      const added = await Promise.all(files.map(async (file) => {
        const asset = await stageInput(file);
        return { ...asset, previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined };
      }));
      setAssets((current) => [...current, ...added]);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "附件添加失败"); }
    finally { setBusy(false); }
  }

  async function remove(asset: StagedAsset) {
    await deleteStagedInput(asset.id).catch(() => undefined);
    if (asset.previewUrl) URL.revokeObjectURL(asset.previewUrl);
    setAssets((current) => current.filter((item) => item.id !== asset.id));
  }

  async function submit() {
    if (!ready) { setError("请填写任务名称和描述，并等待附件处理完成。"); return; }
    setBusy(true); setError("");
    const request: CreateTaskRequest = {
      id: newTaskId(), name: name.trim(), mode: sceneId, goal: description.trim(), modeOptions: { mode: sceneId },
      input: { text: "", fileIds: assets.map((asset) => asset.id) }, executionPolicy: emptyPolicy, agentModels,
    };
    try { const task = await createTask(request); onCreated(task.task_id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建任务失败"); setBusy(false); }
  }

  function onFiles(event: ChangeEvent<HTMLInputElement>) { void upload(Array.from(event.target.files ?? [])); event.target.value = ""; }
  function onDrop(event: DragEvent<HTMLDivElement>) { event.preventDefault(); void upload(Array.from(event.dataTransfer.files)); }
  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.items).filter((item) => item.kind === "file").map((item) => item.getAsFile()).filter((file): file is File => Boolean(file));
    if (files.length) { event.preventDefault(); void upload(files); }
  }

  return <div className="new-task-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
    <section className="new-task-modal" role="dialog" aria-modal="true" aria-labelledby="new-task-title">
      <header className="new-task-modal-head"><div><h1 id="new-task-title">创建任务</h1><p>选择场景并提供初始提示，也可以为本任务覆盖 Solver 默认模型。</p></div><button className="icon-button" aria-label="关闭" onClick={onCancel}><X size={20} /></button></header>
      <div className="new-task-modal-body">
        <label className="wide">任务名称<input autoFocus maxLength={100} value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：JWT 密钥泄露" /></label>
        <fieldset className="scene-picker"><legend>题目类型：场景</legend><div className="scene-card-grid">
          {profiles.filter((profile) => TASK_MODES.includes(profile.id)).map((profile) => <button type="button" key={profile.id} className={sceneId === profile.id ? "selected" : ""} onClick={() => setSceneId(profile.id)}><span className="scene-code">{SCENE_ICONS[profile.id]}</span><strong>{profile.label}</strong><small>{profile.description}</small>{sceneId === profile.id ? <Check size={17} /> : null}</button>)}
        </div></fieldset>
        <label className="wide task-description-field">描述<span>用户初始提示词</span><textarea rows={7} value={description} onChange={(event) => setDescription(event.target.value)} onPaste={onPaste} placeholder="粘贴题目描述、授权目标、已知信息和期望结果；也可以直接粘贴图片。" /></label>
        <div className="task-attachment-zone" onDragOver={(event) => event.preventDefault()} onDrop={onDrop}><input ref={inputRef} type="file" multiple hidden onChange={onFiles} /><button type="button" className="ref-secondary-button" onClick={() => inputRef.current?.click()}><Paperclip size={16} />添加文件或图片</button><span>支持拖放、文件选择以及在描述框粘贴图片</span></div>
        {assets.length ? <div className="task-attachment-list">{assets.map((asset) => <article key={asset.id}>{asset.previewUrl ? <img src={asset.previewUrl} alt="" /> : asset.mediaKind === "image" ? <Image size={22} /> : <FileText size={22} />}<span><strong>{asset.originalName}</strong><small>{formatBytes(asset.size)}</small></span><button aria-label={`移除 ${asset.originalName}`} onClick={() => void remove(asset)}><X size={15} /></button></article>)}</div> : null}
        {agentOptions ? <fieldset className="task-agent-models"><legend>本任务 Solver 模型</legend><p>默认继承 Solver 配置；这里的选择只影响本任务。</p><div>{agentOptions.agents.map((agent) => {
          const compatible = agentOptions.models.filter((model) => modelCompatible(agent.runtime, model.protocol));
          const current = agentModels[agent.id];
          return <label key={agent.id}><span><strong>{agent.display_name}</strong><small>{agent.id}</small></span><select aria-label={`${agent.display_name} 模型`} value={current ? `${current.provider_id}::${current.model_id}` : ""} onChange={(event) => { const [provider_id, model_id] = event.target.value.split("::"); setAgentModels((value) => ({ ...value, [agent.id]: { provider_id, model_id } })); }}>
            {compatible.map((model) => <option key={`${model.provider_id}::${model.model_id}`} value={`${model.provider_id}::${model.model_id}`} disabled={!model.ready}>{model.provider_name} / {model.model_name}{model.ready ? "" : "（密钥不可用）"}</option>)}
          </select></label>;
        })}</div></fieldset> : null}
        <aside className="new-task-config-note"><strong>{selected?.label ?? "场景"}提示词将自动进入黑板</strong><p>任务启动时写入场景提示词、当前描述和附件索引；上面的模型选择会保存为本任务 Agent 快照。</p></aside>
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
      </div>
      <footer className="new-task-modal-actions"><button type="button" className="ref-secondary-button" disabled={busy} onClick={onCancel}>取消</button><button type="button" className="ref-primary-button" disabled={!ready} onClick={() => void submit()}><Play size={16} />{busy ? "创建中…" : "创建并启动"}</button></footer>
    </section>
  </div>;
}

function formatBytes(value: number) { return value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`; }
function modelCompatible(runtime: "openai_agents" | "claude_agent", protocol: "openai_responses" | "openai_chat_completions" | "anthropic") { return runtime === "claude_agent" ? protocol === "anthropic" : protocol !== "anthropic"; }
