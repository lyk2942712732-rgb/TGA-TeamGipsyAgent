import { ChangeEvent, ClipboardEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  createTask, deleteStagedInput, fetchModeProfiles, preflightTask, stageInput,
  fetchSkillSettings, fetchAgentModelOptions,
  type CreateTaskRequest, type ExecutionPolicy, type ModeConfig,
  type AgentModelOptions, type ModeProfileContract, type SkillSetting, type StagedAsset, type TaskPreflight,
} from "../api/tasks";
import { AlertTriangle, Check, Code2, Cpu, Crosshair, Search, ShieldCheck, ShieldPlus, Sparkles, Users } from "lucide-react";
import type { ReactNode } from "react";
import { TASK_MODES, type TaskMode } from "../modes";
import { NewTaskGuide, NewTaskHeader, NewTaskProgress } from "../features/tasks/create/NewTaskProgress";

export function newTaskId(): string {
  const uuid = globalThis.crypto?.randomUUID;
  if (typeof uuid === "function") return `task_${uuid.call(globalThis.crypto).replace(/-/g, "").slice(0, 12)}`;
  return `task_${`${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`.slice(0, 12).padEnd(12, "0")}`;
}

const csv = (value: string) => value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
const join = (value: unknown) => Array.isArray(value) ? value.join(", ") : "";
const bytes = (value: number) => value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`;

const emptyPolicy: ExecutionPolicy = {
  preset: "offline_analysis",
  network: {
    access: "disabled", interaction: "observe",
    seed_origins: [], custom_origins: [], custom_domains: [], custom_cidrs: [], deny_private_networks: true, deny_loopback: true,
    deny_link_local: true, deny_cloud_metadata: true, rate_limit_per_minute: 1, concurrency: 1, request_timeout_seconds: 1,
  },
  local_compute: { mode: "disabled", timeout_seconds: 1, concurrency: 1, network_inheritance: "task_network_policy" },
  high_impact: { mode: "forbidden", allowed_actions: [] },
};

const copyPolicy = (value: ExecutionPolicy): ExecutionPolicy => structuredClone(value);

type Draft = { id: string; name: string; mode: TaskMode; goal: string; modeOptions: ModeConfig; executionPolicy: ExecutionPolicy };
type PreflightBlocker = { id: string; message: string; step: number };
const defaultDraft = (): Draft => ({ id: newTaskId(), name: "", mode: "penetration_test", goal: "", modeOptions: { mode: "penetration_test" }, executionPolicy: structuredClone(emptyPolicy) });

const MODE_CARD_META: Record<TaskMode, { icon: typeof Crosshair; tone: string }> = {
  penetration_test: { icon: ShieldCheck, tone: "blue" },
  incident_response: { icon: ShieldPlus, tone: "green" },
  vulnerability_research: { icon: Search, tone: "orange" },
  reverse_engineering: { icon: Code2, tone: "indigo" },
  pwn: { icon: Crosshair, tone: "violet" },
  security_misc: { icon: Sparkles, tone: "green" },
  cryptography: { icon: ShieldCheck, tone: "orange" },
  forensics: { icon: Search, tone: "blue" },
};

export function NewTaskPage({ onCreated }: { onCreated: (id: string) => void }) {
  const [draft, setDraft] = useState(defaultDraft);
  const [profiles, setProfiles] = useState<Partial<Record<TaskMode, ModeProfileContract>>>({});
  const [step, setStep] = useState(1);
  const [inputFiles, setInputFiles] = useState<StagedAsset[]>([]);
  const [prompt, setPrompt] = useState("");
  const [instructions, setInstructions] = useState("");
  const [constraints, setConstraints] = useState("");
  const [successCriteria, setSuccessCriteria] = useState("");
  const [draftSaved, setDraftSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [preflight, setPreflight] = useState<TaskPreflight | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [preflightError, setPreflightError] = useState("");
  const [skillCatalog, setSkillCatalog] = useState<SkillSetting[]>([]);
  const [skillCatalogLoading, setSkillCatalogLoading] = useState(false);
  const [skillCatalogError, setSkillCatalogError] = useState("");
  const [agentModelOptions, setAgentModelOptions] = useState<AgentModelOptions | null>(null);
  const [agentModelsError, setAgentModelsError] = useState("");
  const draftTouched = useRef(false);
  const uploadControllers = useRef(new Map<string, AbortController>());
  const cancelledUploads = useRef(new Set<string>());
  const filesRef = useRef<StagedAsset[]>([]);
  const profile = profiles[draft.mode];
  const taskPrompt = useMemo(() => [
    instructions.trim() ? `Instructions:\n${instructions.trim()}` : "",
    constraints.trim() ? `Constraints:\n${constraints.trim()}` : "",
    successCriteria.trim() ? `Success Criteria:\n${successCriteria.trim()}` : "",
    prompt.trim(),
  ].filter(Boolean).join("\n\n"), [instructions, constraints, successCriteria, prompt]);
  const preflightBlockers = useMemo(() => {
    const blockers: PreflightBlocker[] = [];
    if (!draft.name.trim()) blockers.push({ id: "task_name", message: "填写任务名称", step: 1 });
    if (!draft.goal.trim()) blockers.push({ id: "task_goal", message: "填写 Objective（任务目标）", step: 1 });
    const incompleteFiles = inputFiles.filter((item) => item.status !== "uploaded");
    if (incompleteFiles.length) blockers.push({
      id: "input_files",
      message: `处理未完成的附件：${incompleteFiles.map((item) => item.originalName).join("、")}`,
      step: 2,
    });
    if (!inputFiles.length && !taskPrompt) blockers.push({
      id: "task_input", message: "填写任务说明或添加至少一个附件", step: 2,
    });
    if (!agentModelOptions) {
      blockers.push({ id: "model_options", message: "等待 Agent 模型清单加载完成", step: 4 });
    } else {
      const unavailableAgents = agentModelOptions.agents.filter((agent) => !agent.model.ready);
      if (unavailableAgents.length) blockers.push({
        id: "agent_models",
        message: `请先在 Solver 页面修复以下模型配置：${unavailableAgents.map((agent) => agent.id).join("、")}`,
        step: 4,
      });
    }
    return blockers;
  }, [draft.name, draft.goal, inputFiles, taskPrompt, agentModelOptions]);
  const completedSteps = useMemo(() => {
    const blocked = new Set(preflightBlockers.map((blocker) => blocker.step));
    return new Set([1, 2, 3, 4].filter((number) => !blocked.has(number)));
  }, [preflightBlockers]);

  useEffect(() => {
    const saved = localStorage.getItem("tga-new-task-draft");
    if (!saved) return;
    try {
      const value = JSON.parse(saved) as Partial<{
        draft: Draft; instructions: string; constraints: string; successCriteria: string; prompt: string;
      }>;
      if (value.draft && TASK_MODES.includes(value.draft.mode)) {
        draftTouched.current = true;
        setDraft(value.draft);
        setInstructions(value.instructions ?? "");
        setConstraints(value.constraints ?? "");
        setSuccessCriteria(value.successCriteria ?? "");
        setPrompt(value.prompt ?? "");
        setDraftSaved(true);
      }
    } catch {
      localStorage.removeItem("tga-new-task-draft");
    }
  }, []);

  useEffect(() => {
    setError((current) => {
      if (!current?.startsWith("启动前还需完成：")) return current;
      return preflightBlockers.some((blocker) => current.includes(blocker.message))
        ? current
        : null;
    });
  }, [preflightBlockers]);

  filesRef.current = inputFiles;

  useEffect(() => {
    void fetchModeProfiles().then((contract) => {
      const mapped = Object.fromEntries(contract.profiles.map((item) => [item.id, item])) as Record<TaskMode, ModeProfileContract>;
      setProfiles(mapped);
      if (mapped.penetration_test && !draftTouched.current) setDraft((value) => ({ ...value, goal: value.goal || mapped.penetration_test.default_goal, modeOptions: structuredClone(mapped.penetration_test.default_mode_config), executionPolicy: copyPolicy(mapped.penetration_test.default_execution_policy) }));
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取后端模式契约"));
    setSkillCatalogLoading(true);
    void fetchSkillSettings().then((value) => setSkillCatalog(value.skills)).catch((reason: unknown) => {
      setSkillCatalogError(reason instanceof Error ? reason.message : "无法读取 Skill 包列表");
    }).finally(() => setSkillCatalogLoading(false));
  }, []);

  useEffect(() => {
    let current = true;
    setAgentModelsError("");
    void fetchAgentModelOptions(draft.mode).then((value) => {
      if (!current) return;
      setAgentModelOptions(value);
      const unavailable = value.agents.filter((agent) => !agent.model.ready);
      if (unavailable.length) setAgentModelsError(`以下 Agent 的模型或密钥不可用：${unavailable.map((agent) => agent.id).join("、")}。请分别前往 Solver 和 Models 页面配置。`);
    }).catch((reason: unknown) => {
      if (current) setAgentModelsError(reason instanceof Error ? reason.message : "无法读取 Agent 模型选项");
    });
    return () => { current = false; };
  }, [draft.mode]);

  useEffect(() => () => {
    uploadControllers.current.forEach((controller) => controller.abort());
    filesRef.current.forEach((item) => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl); });
  }, []);

  useEffect(() => {
    setPreflight(null);
    setPreflightError("");
    if (step !== 5 || preflightBlockers.length) return;
    let current = true;
    const request: CreateTaskRequest = {
      ...draft,
      name: draft.name.trim(),
      goal: draft.goal.trim(),
      input: { text: taskPrompt, fileIds: inputFiles.map((item) => item.id) },
    };
    setPreflightLoading(true);
    void preflightTask(request).then((value) => {
      if (current) setPreflight(value);
    }).catch((reason: unknown) => {
      if (current) setPreflightError(reason instanceof Error ? reason.message : "启动前检查失败");
    }).finally(() => { if (current) setPreflightLoading(false); });
    return () => { current = false; };
  }, [step, draft, taskPrompt, inputFiles, agentModelOptions, preflightBlockers]);

  const availableMcp: string[] = [];
  const setConfig = (key: string, value: unknown) => { draftTouched.current = true; setDraft((current) => ({ ...current, modeOptions: { ...current.modeOptions, [key]: value } })); };
  const setPolicy = <K extends keyof ExecutionPolicy>(key: K, value: ExecutionPolicy[K]) => { draftTouched.current = true; setDraft((current) => ({ ...current, executionPolicy: { ...current.executionPolicy, [key]: value, preset: "custom" } })); };
  const selectPolicyPreset = (preset: ExecutionPolicy["preset"]) => {
    draftTouched.current = true;
    if (preset === "custom") { setDraft((current) => ({ ...current, executionPolicy: { ...current.executionPolicy, preset } })); return; }
    const backendDefault = Object.values(profiles).find((item) => item.default_execution_policy.preset === preset)?.default_execution_policy;
    if (backendDefault) setDraft((current) => ({ ...current, executionPolicy: copyPolicy(backendDefault) }));
  };

  function selectMode(mode: TaskMode) {
    const next = profiles[mode];
    if (!next) return;
    draftTouched.current = true;
    setDraft((current) => ({ ...current, mode, modeOptions: structuredClone(next.default_mode_config), executionPolicy: copyPolicy(next.default_execution_policy) }));
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
    localStorage.removeItem("tga-new-task-draft");
    uploadControllers.current.forEach((controller, id) => { cancelledUploads.current.add(id); controller.abort(); });
    inputFiles.forEach((item) => {
      if (item.status === "uploaded") void deleteStagedInput(item.id).catch(() => undefined);
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
    draftTouched.current = false;
    const defaultProfile = profiles.penetration_test;
    setDraft(defaultProfile ? { ...defaultDraft(), goal: defaultProfile.default_goal, modeOptions: structuredClone(defaultProfile.default_mode_config), executionPolicy: copyPolicy(defaultProfile.default_execution_policy) } : defaultDraft());
    setInputFiles([]); setPrompt(""); setInstructions(""); setConstraints(""); setSuccessCriteria(""); setDraftSaved(false); setPreflight(null); setPreflightError(""); setError(null); setStep(1);
  }

  async function submit() {
    if (preflightBlockers.length) {
      const blocker = preflightBlockers[0];
      setError(`启动前还需完成：${blocker.message}。`);
      setStep(blocker.step);
      return;
    }
    if (!preflight || preflightLoading || preflightError) { setError("启动前检查尚未通过，请修复问题后重试。"); setStep(5); return; }
    const request: CreateTaskRequest = { ...draft, name: draft.name.trim(), goal: draft.goal.trim(), input: { text: taskPrompt, fileIds: inputFiles.map((item) => item.id) }, preflightFingerprint: preflight.fingerprint };
    setBusy(true); setError(null);
    try { const result = await createTask(request); localStorage.removeItem("tga-new-task-draft"); onCreated(result.task_id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建任务失败"); }
    finally { setBusy(false); }
  }

  if (!profile) return <section className="ref-page new-task-wizard"><NewTaskHeader /><div className="ref-card form-surface"><p>{error ?? "正在从 .config/scenes.json 读取场景配置…"}</p></div></section>;

  return <section className="ref-page new-task-wizard">
    <NewTaskHeader />
    <div className="wizard-body ref-fill">
    <section className="ref-card form-surface">
      <NewTaskProgress step={step} completedSteps={completedSteps} onStep={setStep} />
      {step === 1 ? <TaskGoalStep draft={draft} profiles={profiles} instructions={instructions} constraints={constraints} successCriteria={successCriteria} onDraft={(patch) => { draftTouched.current = true; setDraft((current) => ({ ...current, ...patch })); }} onMode={selectMode} onInstructions={setInstructions} onConstraints={setConstraints} onSuccessCriteria={setSuccessCriteria} /> : null}
      {step === 2 ? <div className="wizard-step-stack"><ModeFields profile={profile} config={draft.modeOptions} setConfig={setConfig} /><fieldset className="span-2 multimodal-step"><legend>任务提示与材料</legend><p className="field-help">补充目标网址、代码片段或附件。附件会归档到独立 Workspace，图片可直接参与多模态分析。</p><MultimodalComposer text={prompt} assets={inputFiles} busy={busy} onText={setPrompt} onFiles={upload} onRemove={removeAsset} /></fieldset></div> : null}
      {step === 3 ? <fieldset className="span-2"><legend>第三步：黑板协作架构</legend><p className="field-help">当前只有黑板架构，不需要选择解题架构或调度模型。</p><div className="team-model-grid"><article><span>共享状态</span><strong>PostgreSQL 黑板</strong><small>存放用户提示、文件索引、Finding、Q&amp;A、建议与最终候选</small></article><article><span>并行执行</span><strong>两个 Worker 容器</strong><small>通过受控黑板工具同步，不直接互相通信</small></article><article><span>结束阶段</span><strong>Reporter Writeup</strong><small>读取最终黑板快照生成 Markdown</small></article></div></fieldset> : null}
      {step === 4 ? <TeamModelStep modeLabel={profile.label} availableMcp={availableMcp} skills={skillCatalog} skillsLoading={skillCatalogLoading} skillsError={skillCatalogError} modelOptions={agentModelOptions} modelError={agentModelsError} /> : null}
      {step === 5 ? <fieldset className="span-2 preflight-summary"><legend>第五步：启动前检查</legend><dl className="creation-summary"><dt>场景</dt><dd>{profile.label}</dd><dt>任务</dt><dd>{draft.name || "尚未填写"}</dd><dt>任务目标</dt><dd>{draft.goal || "尚未填写"}</dd><dt>任务说明</dt><dd>{taskPrompt || "无文字说明"}</dd><dt>附件（{inputFiles.length}）</dt><dd>{inputFiles.map((item) => item.originalName).join("；") || "无"}</dd><dt>协作架构</dt><dd>PostgreSQL 黑板；Supervisor 顾问；双 Worker 容器并行；Reporter 最终总结</dd><dt>Agent 模型</dt><dd>{agentModelOptions?.agents.map((agent) => `${agent.id} → ${agent.model.provider_name} / ${agent.model.model_name}`).join("；") || "正在读取 Solver 配置"}</dd><dt>共享 Skill 包</dt><dd>{skillCatalog.length} 个可供 Agent 按名称检索和读取。</dd><dt>Worker 工具</dt><dd>Shell / Bash、文件读写、Glob / Grep、容器安全工具和黑板 MCP</dd><dt>完成条件</dt><dd>{successCriteria || `${profile.completion_validator}：${profile.report_sections.join("、") || "证据支持的场景结论"}`}</dd><dt>启动前检查</dt><dd><PreflightSummary value={preflight} loading={preflightLoading} error={preflightError} blockers={preflightBlockers} /></dd></dl></fieldset> : null}
      {error ? <p role="alert" className="inline-error span-2">{error}</p> : null}
    </section>
    <NewTaskGuide modeLabel={profile.label} modeDescription={profile.description} />
    <footer className="wizard-actions"><button type="button" className="secondary-button" disabled={busy} onClick={reset}>重置</button><span className="wizard-save-state" aria-live="polite">{draftSaved ? <><Check size={14} />草稿已保存并会自动恢复</> : null}</span><div>{step > 1 ? <button type="button" disabled={busy} onClick={() => setStep((value) => Math.max(1, value - 1))}>上一步</button> : null}<button type="button" className="save-draft-button" disabled={busy} onClick={() => { localStorage.setItem("tga-new-task-draft", JSON.stringify({ draft, instructions, constraints, successCriteria, prompt })); setDraftSaved(true); }}>保存草稿</button>{step < 5 ? <button type="button" disabled={busy} onClick={() => setStep((value) => Math.min(5, value + 1))}>下一步</button> : <button type="button" disabled={busy || preflightLoading || Boolean(preflightError) || (!preflight && !preflightBlockers.length)} onClick={() => void submit()}>{busy ? "处理中..." : preflightLoading ? "正在检查..." : "创建任务并开始"}</button>}</div></footer>
    </div>
  </section>;
}

function TaskGoalStep({ draft, profiles, instructions, constraints, successCriteria, onDraft, onMode, onInstructions, onConstraints, onSuccessCriteria }: { draft: Draft; profiles: Partial<Record<TaskMode, ModeProfileContract>>; instructions: string; constraints: string; successCriteria: string; onDraft: (patch: Partial<Draft>) => void; onMode: (mode: TaskMode) => void; onInstructions: (value: string) => void; onConstraints: (value: string) => void; onSuccessCriteria: (value: string) => void }) {
  return <div className="task-goal-step">
    <FieldWithCount label="任务名称" required value={draft.name} max={100}><input aria-label="任务名称" value={draft.name} maxLength={100} onChange={(event) => onDraft({ name: event.target.value })} placeholder="请输入任务名称（建议清晰、简洁）" /></FieldWithCount>
    <fieldset className="scene-picker"><legend>题目类型：场景 <sup>*</sup></legend><div className="mode-card-grid">{TASK_MODES.flatMap((mode) => { const profile = profiles[mode]; if (!profile) return []; const meta = MODE_CARD_META[mode]; const Icon = meta.icon; const selected = draft.mode === mode; return [<button type="button" key={mode} className={`mode-card tone-${meta.tone} ${selected ? "selected" : ""}`} aria-pressed={selected} aria-label={`${profile.label}：${profile.description}`} title={profile.description} onClick={() => onMode(mode)}><span className="mode-card-icon"><Icon size={30} /></span>{selected ? <span className="mode-card-check"><Check size={13} /></span> : null}<strong>{profile.label}</strong><span>{profile.description}</span></button>]; })}</div></fieldset>
    <FieldWithCount label="Objective" required info="核心目标与期望结果" value={draft.goal} max={500}><textarea aria-label="Objective" value={draft.goal} maxLength={500} onChange={(event) => onDraft({ goal: event.target.value })} placeholder="请描述本次任务的核心目标与期望达成的结果，例如：发现目标系统中的高危漏洞并获取可复现的利用链。" /></FieldWithCount>
    <FieldWithCount label="Instructions" info="任务背景、范围和完成要求" value={instructions} max={2000}><textarea aria-label="Instructions" value={instructions} maxLength={2000} onChange={(event) => onInstructions(event.target.value)} placeholder="请提供详细的任务背景、范围、优先级、关注点及完成任务的具体要求。" /></FieldWithCount>
    <FieldWithCount label="Constraints" info="任务边界与限制" value={constraints} max={1000}><textarea aria-label="Constraints" value={constraints} maxLength={1000} onChange={(event) => onConstraints(event.target.value)} placeholder="请列出任务的边界条件与限制，例如：禁止暴力破解、仅在指定时间段扫描、不得影响生产环境等。" /></FieldWithCount>
    <FieldWithCount label="Success Criteria" info="任务完成的判断标准" value={successCriteria} max={500}><textarea aria-label="Success Criteria" value={successCriteria} maxLength={500} onChange={(event) => onSuccessCriteria(event.target.value)} placeholder="请定义任务完成的判定标准，例如：发现 ≥ 3 个高危漏洞并验证，或完成内网横向移动并获取域管理员权限等。" /></FieldWithCount>
  </div>;
}

function FieldWithCount({ label, required = false, info, value, max, children }: { label: string; required?: boolean; info?: string; value: string; max: number; children: ReactNode }) {
  return <label className="wizard-counted-field"><span><b>{label}{required ? <sup>*</sup> : null}</b>{info ? <small title={info}>i</small> : null}</span>{children}<em>{value.length}/{max}</em></label>;
}

function TeamModelStep({ modeLabel, availableMcp, skills, skillsLoading, skillsError, modelOptions, modelError }: { modeLabel: string; availableMcp: string[]; skills: SkillSetting[]; skillsLoading: boolean; skillsError: string; modelOptions: AgentModelOptions | null; modelError: string }) {
  return <div className="team-model-step">
    <header><span><Users size={18} /></span><div><h2>团队与模型装配</h2><p>「{modeLabel}」任务使用固定角色拓扑；Worker 会从共享知识库按需检索 Skill。</p></div></header>
    <div className="team-model-grid"><article><span>固定拓扑</span><strong>Supervisor + 双 Worker + Reporter</strong><small>两个 Worker 并行运行在独立容器</small></article><article><span>模型绑定</span><strong>每个 Agent 独立配置</strong><small>角色模型来自 config/agents.json</small></article><article><span>协作方式</span><strong>PostgreSQL 黑板</strong><small>提示、Finding、Q&amp;A 与最终候选共享</small></article></div>
    <section className="agent-model-assignment"><header><div><Cpu size={17} /><h3>Agent 模型（只读）</h3></div><a href="/settings/solvers">前往 Solver 配置</a></header>{modelError ? <p className="team-model-empty"><AlertTriangle size={15} />{modelError}</p> : <div className="agent-model-grid">{modelOptions?.agents.map((agent) => <div className="agent-model-readonly" key={agent.id}><span><strong>{agent.id}</strong><small>{roleLabel(agent.role)}{agent.required ? " · 必需" : " · 按需创建"}</small></span><span><strong>{agent.model.provider_name} / {agent.model.model_name}</strong><small>{agent.model.ready ? "可用" : agent.model.verification_status}</small></span></div>)}</div>}</section>
    <section><header><div><Sparkles size={17} /><h3>共享 Skill 包</h3></div><a href="/settings/skills">管理 Skill</a></header>{skillsLoading ? <p className="team-model-empty">正在读取 .config/skills…</p> : skillsError ? <p className="team-model-empty"><AlertTriangle size={15} />{skillsError}</p> : skills.length ? <><div className="team-model-chips">{skills.slice(0, 12).map((skill) => <span key={skill.name}>{skill.name}</span>)}</div><p className="field-help">不再绑定场景或角色。Worker 会搜索这些包；你也可以在任务说明中明确写“worker 使用某个 Skill 包”。</p></> : <p className="team-model-empty">尚未安装 Skill 包，任务仍可运行。</p>}</section>
    <section><header><div><ShieldCheck size={17} /><h3>Worker 执行工具</h3></div><b>容器内</b></header><div className="team-model-chips"><span>Shell / Bash</span><span>文件读写</span><span>Glob / Grep</span><span>按名读取 Skill</span><span>黑板 MCP</span></div><p className="field-help">具体安全工具来自 Worker 镜像；此页不维护独立 MCP Server 清单。</p></section>
  </div>;
}

function roleLabel(role: AgentModelOptions["agents"][number]["role"]): string {
  return { supervisor: "Supervisor", worker: "Worker", reviewer: "Reviewer", reporter: "Reporter" }[role];
}

function PreflightSummary({ value, loading, error, blockers }: { value: TaskPreflight | null; loading: boolean; error: string; blockers: PreflightBlocker[] }) {
  if (loading) return <div className="creation-skill-status">正在验证模型、输入、策略、Skills 与 MCP 快照…</div>;
  if (error) return <div className="creation-skill-status error" role="alert">检查失败：{error}</div>;
  if (!value && blockers.length) return <div className="preflight-blockers" role="status"><strong>启动前还需完成</strong>{blockers.map((blocker) => <span key={blocker.id}>{blocker.message}</span>)}</div>;
  if (!value) return <div className="creation-skill-status">等待启动前检查结果。</div>;
  return <div className="preflight-checks" data-testid="preflight-passed"><strong>全部检查通过</strong>{value.checks.map((check) => <span key={check.id}>{check.detail}</span>)}</div>;
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

function ModeFields({ profile, config, setConfig }: { profile: ModeProfileContract; config: ModeConfig; setConfig: (key: string, value: unknown) => void }) {
  return <fieldset><legend>{profile.label}配置</legend>{profile.fields.map((field) => {
    const value = config[field.key];
    if (field.type === "select") return <label key={field.key}>{field.label}<select value={String(value ?? "")} onChange={(event) => setConfig(field.key, event.target.value)}>{(field.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}</select></label>;
    if (field.type === "checkbox") return <label key={field.key}><input type="checkbox" checked={Boolean(value)} onChange={(event) => setConfig(field.key, event.target.checked)} />{field.label}</label>;
    if (field.type === "number") return <label key={field.key}>{field.label}<input type="number" min={field.min} max={field.max} value={Number(value ?? field.min ?? 0)} onChange={(event) => setConfig(field.key, Number(event.target.value))} /></label>;
    if (field.type === "textarea") return <label className="span-2" key={field.key}>{field.label}<textarea value={String(value ?? "")} onChange={(event) => setConfig(field.key, event.target.value)} /></label>;
    if (field.type === "csv") return <label key={field.key}>{field.label}<input value={join(value)} onChange={(event) => setConfig(field.key, csv(event.target.value))} /></label>;
    return <label key={field.key}>{field.label}<input value={String(value ?? "")} onChange={(event) => setConfig(field.key, event.target.value)} /></label>;
  })}</fieldset>;
}

function PolicyFields({ draft, setPolicy, selectPreset }: { draft: Draft; setPolicy: <K extends keyof ExecutionPolicy>(key: K, value: ExecutionPolicy[K]) => void; selectPreset: (preset: ExecutionPolicy["preset"]) => void }) {
  const policy = draft.executionPolicy;
  return <fieldset className="span-2"><legend>第三步：授权与执行策略</legend><p className="field-help">默认值来自当前场景的后端 preset。切换为 custom 后可精确调整网络、隔离计算和高影响动作边界。</p>
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
