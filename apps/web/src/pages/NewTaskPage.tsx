import { ChangeEvent, ClipboardEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  createTask, deleteStagedInput, fetchModeProfiles, stageInput,
  type CreateSessionRequest, type ExecutionPolicy, type ModeConfig,
  type ModeProfileContract, type StagedAsset,
} from "../api/tasks";
import { runtimeApi } from "../runtime/api-v2";
import type { MCPHealth } from "../runtime/event-types";
import { MODE_PROFILES, TASK_MODES, type TaskMode } from "../modes";

export function newTaskId(): string {
  const uuid = globalThis.crypto?.randomUUID;
  if (typeof uuid === "function") return `task_${uuid.call(globalThis.crypto).replace(/-/g, "").slice(0, 12)}`;
  return `task_${`${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`.slice(0, 12).padEnd(12, "0")}`;
}

const csv = (value: string) => value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
const join = (value: unknown) => Array.isArray(value) ? value.join(", ") : "";
const bytes = (value: number) => value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`;

const fallbackPolicy = (mode: TaskMode): ExecutionPolicy => ({
  preset: mode === "ctf" ? "autonomous_ctf" : mode === "penetration_test" ? "safe_observation" : "offline_analysis",
  network: {
    access: mode === "ctf" ? "public_internet" : mode === "penetration_test" ? "task_sources" : "disabled",
    interaction: mode === "ctf" ? "interact" : "observe",
    seed_origins: [], custom_origins: [], custom_domains: [], custom_cidrs: [], deny_private_networks: true, deny_loopback: true,
    deny_link_local: true, deny_cloud_metadata: true, rate_limit_per_minute: 30, concurrency: 2,
    request_timeout_seconds: 30,
  },
  local_compute: { mode: "isolated", timeout_seconds: 60, concurrency: 1, network_inheritance: "task_network_policy" },
  high_impact: { mode: mode === "ctf" ? "approval_required" : "forbidden", allowed_actions: [] },
});

const copyPolicy = (value: ExecutionPolicy): ExecutionPolicy => structuredClone(value);

const fallbackConfig = (mode: TaskMode): ModeConfig => {
  if (mode === "ctf") return { mode, subtype: "auto", flag_format: "[A-Za-z0-9_]{2,32}\\{[^{}\\s]{4,200}\\}", expected_flag_count: 1, verifier: { kind: "local_regex" } };
  if (mode === "penetration_test") return { mode, depth: "reconnaissance", included_scopes: [], exclusions: [], rules_of_engagement: "" };
  if (mode === "incident_response") return { mode, phase: "triage", response_authority: "analysis_only", timezone: "UTC", affected_assets: [], known_iocs: [] };
  if (mode === "vulnerability_research") return { mode, depth: "triage", software_version: "", commit: "", allow_fuzzing: false, require_poc: false };
  return { mode, analysis_method: "static_only", sample_type: "auto", platform: "auto", architecture: "auto", analysis_goals: [], expected_outputs: [] };
};

const fallbackProfiles = Object.fromEntries(TASK_MODES.map((mode) => [mode, {
  id: mode, label: MODE_PROFILES[mode].label, description: MODE_PROFILES[mode].description,
  default_goal: MODE_PROFILES[mode].defaultGoal, default_mode_config: fallbackConfig(mode), default_execution_policy: fallbackPolicy(mode),
  allowed_input_kinds: ["file", "archive", "image"], required_conditions: ["prompt_or_files"], recommended_capabilities: [],
  prompt_instruction: "", completion_validator: mode, report_sections: [], uses_flag: mode === "ctf", advanced_settings: [],
  mode_config_schema: {}, execution_policy_schema: {},
}])) as unknown as Record<TaskMode, ModeProfileContract>;

type Draft = { id: string; name: string; mode: TaskMode; goal: string; modeOptions: ModeConfig; executionPolicy: ExecutionPolicy };
const defaultDraft = (): Draft => ({ id: newTaskId(), name: "新建安全任务", mode: "ctf", goal: MODE_PROFILES.ctf.defaultGoal, modeOptions: fallbackConfig("ctf"), executionPolicy: fallbackPolicy("ctf") });

export function NewTaskPage({ onCreated }: { onCreated: (id: string) => void }) {
  const [draft, setDraft] = useState(defaultDraft);
  const [profiles, setProfiles] = useState(fallbackProfiles);
  const [step, setStep] = useState(1);
  const [inputFiles, setInputFiles] = useState<StagedAsset[]>([]);
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<MCPHealth | null>(null);
  const draftTouched = useRef(false);
  const uploadControllers = useRef(new Map<string, AbortController>());
  const cancelledUploads = useRef(new Set<string>());
  const filesRef = useRef<StagedAsset[]>([]);
  const profile = profiles[draft.mode];

  filesRef.current = inputFiles;

  useEffect(() => {
    void fetchModeProfiles().then((contract) => {
      const mapped = Object.fromEntries(contract.profiles.map((item) => [item.id, item])) as Record<TaskMode, ModeProfileContract>;
      setProfiles({ ...fallbackProfiles, ...mapped });
      if (mapped.ctf && !draftTouched.current) setDraft((value) => ({ ...value, goal: mapped.ctf.default_goal, modeOptions: structuredClone(mapped.ctf.default_mode_config), executionPolicy: copyPolicy(mapped.ctf.default_execution_policy) }));
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取后端模式契约"));
    void runtimeApi.toolHealth().then(setHealth).catch(() => undefined);
  }, []);

  useEffect(() => () => {
    uploadControllers.current.forEach((controller) => controller.abort());
    filesRef.current.forEach((item) => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl); });
  }, []);

  const availableMcp = useMemo(() => (
    health?.records ?? []
  ).filter((item) => item.server && item.configured && item.enabled && (item.discovered || item.reachable)), [health]);
  const setConfig = (key: string, value: unknown) => { draftTouched.current = true; setDraft((current) => ({ ...current, modeOptions: { ...current.modeOptions, [key]: value } })); };
  const setPolicy = <K extends keyof ExecutionPolicy>(key: K, value: ExecutionPolicy[K]) => { draftTouched.current = true; setDraft((current) => ({ ...current, executionPolicy: { ...current.executionPolicy, [key]: value, preset: "custom" } })); };
  const selectPolicyPreset = (preset: ExecutionPolicy["preset"]) => {
    draftTouched.current = true;
    if (preset === "custom") { setDraft((current) => ({ ...current, executionPolicy: { ...current.executionPolicy, preset } })); return; }
    const backendDefault = Object.values(profiles).find((item) => item.default_execution_policy.preset === preset)?.default_execution_policy;
    setDraft((current) => ({ ...current, executionPolicy: copyPolicy(backendDefault ?? fallbackPolicy(preset === "autonomous_ctf" ? "ctf" : preset === "safe_observation" ? "penetration_test" : "incident_response")) }));
  };

  function selectMode(mode: TaskMode) {
    const next = profiles[mode] ?? fallbackProfiles[mode];
    draftTouched.current = true;
    setDraft((current) => ({ ...current, mode, goal: next.default_goal, modeOptions: structuredClone(next.default_mode_config), executionPolicy: copyPolicy(next.default_execution_policy) }));
  }

  async function upload(files: File[]) {
    if (!files.length) return;
    setBusy(true); setError(null);
    const placeholders = files.map((file, index): StagedAsset => ({
      id: `uploading_${Date.now()}_${index}`, originalName: file.name, mimeType: file.type || "application/octet-stream",
      mediaKind: file.type.startsWith("image/") ? "image" : "other", size: file.size, sha256: "", status: "uploading",
      previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined,
    }));
    setInputFiles((current) => [...current, ...placeholders]);
    await Promise.all(files.map(async (file, index) => {
      const placeholder = placeholders[index];
      const controller = new AbortController();
      uploadControllers.current.set(placeholder.id, controller);
      try {
        const asset = await stageInput(file, controller.signal);
        if (cancelledUploads.current.has(placeholder.id)) { await deleteStagedInput(asset.id).catch(() => undefined); return; }
        setInputFiles((current) => current.map((item) => item.id === placeholder.id ? { ...asset, previewUrl: placeholder.previewUrl } : item));
      } catch (reason) {
        if (controller.signal.aborted) return;
        const message = reason instanceof Error ? reason.message : "上传失败";
        setInputFiles((current) => current.map((item) => item.id === placeholder.id ? { ...item, status: "failed", error: message } : item));
        setError(`${file.name}: ${message}`);
      } finally { uploadControllers.current.delete(placeholder.id); cancelledUploads.current.delete(placeholder.id); }
    }));
    setBusy(false);
  }

  async function removeAsset(asset: StagedAsset) {
    if (asset.status === "uploading") { cancelledUploads.current.add(asset.id); uploadControllers.current.get(asset.id)?.abort(); }
    if (asset.status === "uploaded") await deleteStagedInput(asset.id).catch(() => undefined);
    if (asset.previewUrl) URL.revokeObjectURL(asset.previewUrl);
    setInputFiles((current) => current.filter((item) => item.id !== asset.id));
  }

  function reset() {
    uploadControllers.current.forEach((controller, id) => { cancelledUploads.current.add(id); controller.abort(); });
    inputFiles.forEach((item) => {
      if (item.status === "uploaded") void deleteStagedInput(item.id).catch(() => undefined);
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
    draftTouched.current = false; setDraft(defaultDraft()); setInputFiles([]); setPrompt(""); setError(null); setStep(1);
  }

  async function submit() {
    if (!draft.name.trim() || !draft.goal.trim()) { setError("请填写任务名称和任务目标。"); setStep(2); return; }
    if (inputFiles.some((item) => item.status !== "uploaded")) { setError("请先处理仍在上传或上传失败的文件。"); setStep(3); return; }
    if (!inputFiles.length && !prompt.trim()) { setError("请在提示词中写下任务要求，或添加至少一个附件。"); setStep(3); return; }
    const request: CreateSessionRequest = { ...draft, name: draft.name.trim(), goal: draft.goal.trim(), input: { text: prompt.trim(), fileIds: inputFiles.map((item) => item.id) } };
    setBusy(true); setError(null);
    try { const result = await createTask(request); onCreated(result.task_id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建任务失败"); }
    finally { setBusy(false); }
  }

  return <section className="page-stack new-task-wizard">
    <header className="page-title"><div><span className="eyebrow">TASKS / CREATE</span><h1>新建任务</h1><p>选择任务场景，提供任务提示词与材料；执行边界独立控制，匹配场景的 Skills 和已启用能力会自动装配。</p></div></header>
    <nav className="wizard-steps" aria-label="创建步骤">{["选择场景", "任务配置", "任务提示与材料", "执行边界", "创建摘要"].map((label, index) => <button key={label} type="button" className={`${step === index + 1 ? "active" : ""} ${step > index + 1 ? "complete" : ""}`} onClick={() => setStep(index + 1)}><b>{step > index + 1 ? "✓" : index + 1}</b><span>{label}</span></button>)}</nav>
    <section className="surface form-surface">
      {step === 1 ? <fieldset className="span-2 scene-picker"><legend>第一步：选择场景</legend><p className="field-help">场景决定方法论、完成条件以及任务运行时可加载的 Skills。</p><div className="mode-card-grid">{TASK_MODES.map((mode) => <button type="button" key={mode} className={`mode-card ${draft.mode === mode ? "selected" : ""}`} onClick={() => selectMode(mode)}><small>{String(TASK_MODES.indexOf(mode) + 1).padStart(2, "0")}</small><strong>{profiles[mode].label}</strong><span>{profiles[mode].description}</span><em>{draft.mode === mode ? "当前场景" : "选择此场景"}</em></button>)}</div></fieldset> : null}
      {step === 2 ? <><fieldset><legend>第二步：任务配置</legend><label>任务名称<input value={draft.name} onChange={(event) => { draftTouched.current = true; setDraft((current) => ({ ...current, name: event.target.value })); }} /></label><label className="span-2">任务目标与完成标准<textarea value={draft.goal} onChange={(event) => { draftTouched.current = true; setDraft((current) => ({ ...current, goal: event.target.value })); }} /></label></fieldset><ModeFields mode={draft.mode} config={draft.modeOptions} setConfig={setConfig} /></> : null}
      {step === 3 ? <fieldset className="span-2 multimodal-step"><legend>第三步：多模态输入</legend><p className="field-help">把任务要求、网址、代码片段和附件放进同一个提示词窗口。附件会作为任务输入归档到独立 Workspace，图片会在支持视觉的模型中直接参与分析。</p><MultimodalComposer text={prompt} assets={inputFiles} busy={busy} onText={setPrompt} onFiles={upload} onRemove={removeAsset} /></fieldset> : null}
      {step === 4 ? <PolicyFields draft={draft} setPolicy={setPolicy} selectPreset={selectPolicyPreset} /> : null}
      {step === 5 ? <fieldset className="span-2"><legend>第五步：创建摘要</legend><dl className="creation-summary"><dt>场景</dt><dd>{profile.label}</dd><dt>任务</dt><dd>{draft.name}</dd><dt>提示词</dt><dd>{prompt.trim() || "无文字提示词"}</dd><dt>附件（{inputFiles.length}）</dt><dd>{inputFiles.map((item) => item.originalName).join("；") || "无"}</dd><dt>执行边界</dt><dd>preset={draft.executionPolicy.preset}；network={draft.executionPolicy.network.access}/{draft.executionPolicy.network.interaction}；compute={draft.executionPolicy.local_compute.mode}；high_impact={draft.executionPolicy.high_impact.mode}</dd><dt>自动可用 MCP（{availableMcp.length}）</dt><dd>{availableMcp.map((item) => item.server).join(", ") || "当前无已启用且可达/已发现的 MCP 服务"}</dd><dt>完成条件</dt><dd>{profile.completion_validator}：{profile.report_sections.join("、") || "证据支持的模式专属验证"}</dd></dl></fieldset> : null}
      {error ? <p role="alert" className="inline-error span-2">{error}</p> : null}
      <footer className="wizard-actions span-2"><button type="button" className="secondary-button" disabled={busy} onClick={reset}>重置</button><div><button type="button" disabled={step === 1 || busy} onClick={() => setStep((value) => Math.max(1, value - 1))}>上一步</button>{step < 5 ? <button type="button" disabled={busy} onClick={() => setStep((value) => Math.min(5, value + 1))}>下一步</button> : <button type="button" disabled={busy} onClick={() => void submit()}>{busy ? "处理中..." : "创建任务并开始"}</button>}</div></footer>
    </section>
  </section>;
}

function MultimodalComposer({ text, assets, busy, onText, onFiles, onRemove }: { text: string; assets: StagedAsset[]; busy: boolean; onText: (value: string) => void; onFiles: (files: File[]) => void; onRemove: (asset: StagedAsset) => void }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const choose = (event: ChangeEvent<HTMLInputElement>) => { onFiles(Array.from(event.target.files ?? [])); event.target.value = ""; };
  const drop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(false); if (!busy) onFiles(Array.from(event.dataTransfer.files)); };
  const paste = (event: ClipboardEvent<HTMLTextAreaElement>) => { const files = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/")); if (files.length) { event.preventDefault(); onFiles(files); } };
  return <section className={`multimodal-composer ${dragging ? "active" : ""} ${busy ? "busy" : ""}`} onDragOver={(event) => { event.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }} onDrop={drop}>
    <div className="composer-prompt"><div className="composer-prompt-label"><span className="composer-spark">✦</span><strong>提示词</strong><small>描述你希望 Agent 完成什么，以及它应该关注哪些细节</small></div><textarea aria-label="任务提示词" value={text} onChange={(event) => onText(event.target.value)} onPaste={paste} placeholder="例如：分析这些文件，找出入口点并给出可验证的下一步……\n\n你也可以直接把截图粘贴到这里，或把文件拖进窗口。" /></div>
    <div className="composer-footer"><div className="composer-hint"><span className="composer-attach-icon">+</span><span>{dragging ? "松开以上传附件" : "拖拽文件到这里，或"}</span><button type="button" className="text-button" onClick={() => inputRef.current?.click()} disabled={busy}>选择文件</button><input ref={inputRef} type="file" multiple disabled={busy} onChange={choose} /><small>支持多文件、图片和粘贴</small></div><span className="composer-count">{text.length} 字符 · {assets.length} 个附件</span></div>
    {assets.length ? <div className="composer-attachments" aria-label="已添加附件">{assets.map((asset) => <article key={asset.id} className={asset.status}>{asset.previewUrl ? <img src={asset.previewUrl} alt={`${asset.originalName} 缩略图`} /> : <span className="file-kind-mark">{asset.mediaKind.slice(0, 3).toUpperCase()}</span>}<div><strong title={asset.originalName}>{asset.originalName}</strong><small>{asset.mimeType} · {bytes(asset.size)}</small>{asset.error ? <em>{asset.error}</em> : null}</div><span className={`status-badge ${asset.status}`}>{asset.status === "uploading" ? "上传中" : asset.status === "failed" ? "失败" : "已上传"}</span><button type="button" className="icon-button" aria-label={`删除 ${asset.originalName}`} onClick={() => onRemove(asset)}>×</button></article>)}</div> : null}
    {dragging ? <div className="composer-drop-overlay">放开文件，添加到提示词</div> : null}
  </section>;
}

function ModeFields({ mode, config, setConfig }: { mode: TaskMode; config: ModeConfig; setConfig: (key: string, value: unknown) => void }) {
  if (mode === "ctf") return <fieldset><legend>CTF 配置</legend><label>CTF 子类型<select value={String(config.subtype ?? "auto")} onChange={(event) => setConfig("subtype", event.target.value)}>{["auto", "web", "pwn", "reverse", "crypto", "misc", "forensics", "unknown"].map((item) => <option key={item}>{item}</option>)}</select></label><label>Flag 格式（可选）<input value={String(config.flag_format ?? "")} onChange={(event) => setConfig("flag_format", event.target.value || null)} /></label></fieldset>;
  if (mode === "penetration_test") return <fieldset><legend>渗透测试配置</legend><label>测试深度<select value={String(config.depth ?? "reconnaissance")} onChange={(event) => setConfig("depth", event.target.value)}><option value="reconnaissance">reconnaissance</option><option value="validation">validation</option><option value="comprehensive">comprehensive</option></select></label><label>包含范围<input value={join(config.included_scopes)} onChange={(event) => setConfig("included_scopes", csv(event.target.value))} /></label><label>排除范围<input value={join(config.exclusions)} onChange={(event) => setConfig("exclusions", csv(event.target.value))} /></label><label className="span-2">Rules of Engagement<textarea value={String(config.rules_of_engagement ?? "")} onChange={(event) => setConfig("rules_of_engagement", event.target.value)} /></label></fieldset>;
  if (mode === "incident_response") return <fieldset><legend>应急响应配置</legend><label>事件阶段<select value={String(config.phase ?? "triage")} onChange={(event) => setConfig("phase", event.target.value)}>{["triage", "investigation", "containment", "eradication", "recovery", "post-incident"].map((item) => <option key={item}>{item}</option>)}</select></label><label>响应权限<select value={String(config.response_authority ?? "analysis_only")} onChange={(event) => setConfig("response_authority", event.target.value)}><option value="analysis_only">analysis_only</option><option value="containment_with_approval">containment_with_approval</option><option value="authorized_containment">authorized_containment</option></select></label></fieldset>;
  if (mode === "vulnerability_research") return <fieldset><legend>漏洞挖掘配置</legend><label>研究深度<select value={String(config.depth ?? "triage")} onChange={(event) => setConfig("depth", event.target.value)}><option value="triage">triage</option><option value="focused">focused</option><option value="deep">deep</option></select></label><label>软件版本<input value={String(config.software_version ?? "")} onChange={(event) => setConfig("software_version", event.target.value)} /></label><label><input type="checkbox" checked={Boolean(config.allow_fuzzing)} onChange={(event) => setConfig("allow_fuzzing", event.target.checked)} />允许 Fuzz</label></fieldset>;
  return <fieldset><legend>逆向分析配置</legend><label>分析方式<select value={String(config.analysis_method ?? "static_only")} onChange={(event) => setConfig("analysis_method", event.target.value)}><option value="static_only">static_only</option><option value="static_and_dynamic">static_and_dynamic</option><option value="deep_instrumentation">deep_instrumentation</option></select></label><label>样本类型<input value={String(config.sample_type ?? "auto")} onChange={(event) => setConfig("sample_type", event.target.value)} /></label><label>目标平台<input value={String(config.platform ?? "auto")} onChange={(event) => setConfig("platform", event.target.value)} /></label><label>架构<input value={String(config.architecture ?? "auto")} onChange={(event) => setConfig("architecture", event.target.value)} /></label></fieldset>;
}

function PolicyFields({ draft, setPolicy, selectPreset }: { draft: Draft; setPolicy: <K extends keyof ExecutionPolicy>(key: K, value: ExecutionPolicy[K]) => void; selectPreset: (preset: ExecutionPolicy["preset"]) => void }) {
  const policy = draft.executionPolicy;
  return <fieldset className="span-2"><legend>第四步：执行边界</legend><p className="field-help">默认值来自当前场景的后端 preset。切换为 custom 后可精确调整网络、隔离计算和高影响动作边界。</p>
    <label>执行策略<select value={policy.preset} onChange={(event) => selectPreset(event.target.value as ExecutionPolicy["preset"])}><option value="autonomous_ctf">自主解题</option><option value="safe_observation">安全观察</option><option value="offline_analysis">离线分析</option><option value="custom">自定义</option></select></label>
    <label>网络访问范围<select value={policy.network.access} onChange={(event) => setPolicy("network", { ...policy.network, access: event.target.value as ExecutionPolicy["network"]["access"] })}><option value="disabled">禁止网络访问</option><option value="task_sources">仅初始任务来源</option><option value="public_internet">公网地址</option><option value="custom">自定义允许列表</option></select></label>
    <p className="field-help span-2">“公网地址”允许任意 HTTP(S) 目标；任务来源、重定向范围、代理禁用、DNS pinning、请求限流和高影响操作审批仍按当前执行策略生效。</p>
    <label>网络交互权限<select value={policy.network.interaction} onChange={(event) => setPolicy("network", { ...policy.network, interaction: event.target.value as ExecutionPolicy["network"]["interaction"] })}><option value="observe">仅观察（GET/HEAD）</option><option value="interact">允许常规交互</option></select></label>
    {policy.network.access === "custom" ? <>
      <label className="span-2">自定义来源（逗号分隔）<input value={policy.network.custom_origins.join(", ")} onChange={(event) => setPolicy("network", { ...policy.network, custom_origins: csv(event.target.value) })} /></label>
      <label className="span-2">自定义域名规则（支持 `*.example.test`）<input value={policy.network.custom_domains.join(", ")} onChange={(event) => setPolicy("network", { ...policy.network, custom_domains: csv(event.target.value) })} /></label>
      <label className="span-2">自定义 CIDR<input value={policy.network.custom_cidrs.join(", ")} onChange={(event) => setPolicy("network", { ...policy.network, custom_cidrs: csv(event.target.value) })} placeholder="例如：198.18.0.0/15" /></label>
    </> : null}
    <label>请求速率 / 分钟<input type="number" min={1} value={policy.network.rate_limit_per_minute} onChange={(event) => setPolicy("network", { ...policy.network, rate_limit_per_minute: Number(event.target.value) })} /></label>
    <label>网络并发<input type="number" min={1} value={policy.network.concurrency} onChange={(event) => setPolicy("network", { ...policy.network, concurrency: Number(event.target.value) })} /></label>
    <label>请求超时（秒）<input type="number" min={1} value={policy.network.request_timeout_seconds} onChange={(event) => setPolicy("network", { ...policy.network, request_timeout_seconds: Number(event.target.value) })} /></label>
    <label>本地计算<select value={policy.local_compute.mode} onChange={(event) => setPolicy("local_compute", { ...policy.local_compute, mode: event.target.value as ExecutionPolicy["local_compute"]["mode"] })}><option value="disabled">关闭本地计算</option><option value="isolated">使用隔离环境</option></select></label><p className="field-help">隔离计算容器不直接联网；所有外部访问必须通过受当前任务网络策略治理的 HTTP 工具执行。</p>
    <label>计算超时（秒）<input type="number" min={1} value={policy.local_compute.timeout_seconds} onChange={(event) => setPolicy("local_compute", { ...policy.local_compute, timeout_seconds: Number(event.target.value) })} /></label>
    <label>计算并发<input type="number" min={1} value={policy.local_compute.concurrency} onChange={(event) => setPolicy("local_compute", { ...policy.local_compute, concurrency: Number(event.target.value) })} /></label>
    <label>高影响动作<select value={policy.high_impact.mode} onChange={(event) => setPolicy("high_impact", { ...policy.high_impact, mode: event.target.value as ExecutionPolicy["high_impact"]["mode"] })}><option value="forbidden">禁止执行</option><option value="approval_required">需人工审批</option><option value="allowlisted">仅允许清单内动作</option></select></label>
    {policy.high_impact.mode === "allowlisted" ? <label className="span-2">允许动作（逗号分隔）<input value={policy.high_impact.allowed_actions.join(", ")} onChange={(event) => setPolicy("high_impact", { ...policy.high_impact, allowed_actions: csv(event.target.value) })} /></label> : null}
  </fieldset>;
}
