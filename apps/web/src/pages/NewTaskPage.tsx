import { ChangeEvent, ClipboardEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  createTask, deleteStagedInput, fetchModeProfiles, preflightTask, stageInput,
  fetchSkillSettings, previewTaskSkills, fetchAgentModelOptions,
  type CreateSessionRequest, type ExecutionPolicy, type ModeConfig,
  type AgentModelOptions, type ModeProfileContract, type SkillPreview, type SkillSetting, type StagedAsset, type TaskPreflight,
} from "../api/tasks";
import { AlertTriangle, Check, Code2, Cpu, Crosshair, Search, ShieldCheck, ShieldPlus, Sparkles, Users } from "lucide-react";
import type { ReactNode } from "react";
import { runtimeApi } from "../runtime/api-v2";
import type { MCPHealth } from "../runtime/event-types";
import { MODE_PROFILES, TASK_MODES, type TaskMode } from "../modes";
import { NewTaskGuide, NewTaskHeader, NewTaskProgress } from "../features/tasks/create/NewTaskProgress";

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
  completion_validator: mode, report_sections: [], uses_flag: mode === "ctf", advanced_settings: [],
  mode_config_schema: {}, execution_policy_schema: {},
}])) as unknown as Record<TaskMode, ModeProfileContract>;

type Draft = { id: string; name: string; mode: TaskMode; goal: string; modeOptions: ModeConfig; executionPolicy: ExecutionPolicy };
type PreflightBlocker = { id: string; message: string; step: number };
const defaultDraft = (): Draft => ({ id: newTaskId(), name: "", mode: "penetration_test", goal: "", modeOptions: fallbackConfig("penetration_test"), executionPolicy: fallbackPolicy("penetration_test") });

const MODE_CARD_META: Record<TaskMode, { label: string; icon: typeof Crosshair; tone: string; description: string }> = {
  ctf: { label: "CTF", icon: Crosshair, tone: "violet", description: "面向夺旗赛的自动化解题与攻防验证" },
  penetration_test: { label: "渗透测试", icon: ShieldCheck, tone: "blue", description: "模拟黑客攻击，发现并验证安全风险" },
  incident_response: { label: "应急响应", icon: ShieldPlus, tone: "green", description: "快速定位、遏制与恢复安全事件" },
  vulnerability_research: { label: "漏洞研究", icon: Search, tone: "orange", description: "发现、分析与验证安全漏洞" },
  reverse_engineering: { label: "逆向工程", icon: Code2, tone: "indigo", description: "对二进制文件进行逆向分析与研究" },
};

export function NewTaskPage({ onCreated }: { onCreated: (id: string) => void }) {
  const [draft, setDraft] = useState(defaultDraft);
  const [profiles, setProfiles] = useState(fallbackProfiles);
  const [step, setStep] = useState(1);
  const [inputFiles, setInputFiles] = useState<StagedAsset[]>([]);
  const [prompt, setPrompt] = useState("");
  const [instructions, setInstructions] = useState("");
  const [constraints, setConstraints] = useState("");
  const [successCriteria, setSuccessCriteria] = useState("");
  const [draftSaved, setDraftSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<MCPHealth | null>(null);
  const [skillPreview, setSkillPreview] = useState<SkillPreview | null>(null);
  const [skillPreviewLoading, setSkillPreviewLoading] = useState(false);
  const [skillPreviewError, setSkillPreviewError] = useState("");
  const [preflight, setPreflight] = useState<TaskPreflight | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [preflightError, setPreflightError] = useState("");
  const [selectedSkills, setSelectedSkills] = useState<string[] | null>(null);
  const [skillDialogOpen, setSkillDialogOpen] = useState(false);
  const [skillCatalog, setSkillCatalog] = useState<SkillSetting[]>([]);
  const [skillCatalogLoading, setSkillCatalogLoading] = useState(false);
  const [agentModelOptions, setAgentModelOptions] = useState<AgentModelOptions | null>(null);
  const [agentModels, setAgentModels] = useState<Record<string, { providerId: string; modelId: string }>>({});
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
      const missingAgents = agentModelOptions.agents.filter((agent) => !agentModels[agent.id]);
      if (missingAgents.length) blockers.push({
        id: "agent_models",
        message: `为以下 Agent 选择已验证模型：${missingAgents.map((agent) => agent.id).join("、")}`,
        step: 4,
      });
    }
    return blockers;
  }, [draft.name, draft.goal, inputFiles, taskPrompt, agentModelOptions, agentModels]);
  const completedSteps = useMemo(() => {
    const blocked = new Set(preflightBlockers.map((blocker) => blocker.step));
    return new Set([1, 2, 3, 4].filter((number) => !blocked.has(number)));
  }, [preflightBlockers]);

  filesRef.current = inputFiles;

  useEffect(() => {
    void fetchModeProfiles().then((contract) => {
      const mapped = Object.fromEntries(contract.profiles.map((item) => [item.id, item])) as Record<TaskMode, ModeProfileContract>;
      setProfiles({ ...fallbackProfiles, ...mapped });
      if (mapped.penetration_test && !draftTouched.current) setDraft((value) => ({ ...value, modeOptions: structuredClone(mapped.penetration_test.default_mode_config), executionPolicy: copyPolicy(mapped.penetration_test.default_execution_policy) }));
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取后端模式契约"));
    void runtimeApi.toolHealth().then(setHealth).catch(() => undefined);
  }, []);

  useEffect(() => {
    let current = true;
    setAgentModelsError("");
    void fetchAgentModelOptions(draft.mode).then((value) => {
      if (!current) return;
      setAgentModelOptions(value);
      const ready = value.models.filter((item) => item.ready);
      const fallback = ready[0];
      setAgentModels((existing) => Object.fromEntries(value.agents.flatMap((agent) => {
        const selected = existing[agent.id];
        const valid = ready.some((item) => item.provider_id === selected?.providerId && item.model_id === selected?.modelId);
        const choice = valid ? selected : fallback ? { providerId: fallback.provider_id, modelId: fallback.model_id } : null;
        return choice ? [[agent.id, choice]] : [];
      })));
      if (!fallback) setAgentModelsError("没有已验证的模型。请先前往模型供应商页面完成配置与验证。");
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
    if (![4, 5].includes(step) || !draft.goal.trim()) return;
    let current = true;
    setSkillPreviewLoading(true);
    setSkillPreviewError("");
    void previewTaskSkills({
      mode: draft.mode,
      goal: draft.goal.trim(),
      modeOptions: draft.modeOptions,
      prompt: taskPrompt,
      fileNames: inputFiles.filter((item) => item.status === "uploaded").map((item) => item.originalName),
      executionPolicy: draft.executionPolicy,
      selectedSkills,
    }).then((value) => {
      if (current) setSkillPreview(value);
    }).catch((reason: unknown) => {
      if (!current) return;
      setSkillPreview(null);
      setSkillPreviewError(reason instanceof Error ? reason.message : "无法预览自动装配的 Skills");
    }).finally(() => { if (current) setSkillPreviewLoading(false); });
    return () => { current = false; };
  }, [step, draft.mode, draft.goal, draft.modeOptions, draft.executionPolicy, taskPrompt, inputFiles, selectedSkills]);

  useEffect(() => {
    setPreflight(null);
    setPreflightError("");
    if (step !== 5 || preflightBlockers.length) return;
    let current = true;
    const request: CreateSessionRequest = {
      ...draft,
      name: draft.name.trim(),
      goal: draft.goal.trim(),
      input: { text: taskPrompt, fileIds: inputFiles.map((item) => item.id) },
      selectedSkills,
      agentModels,
    };
    setPreflightLoading(true);
    void preflightTask(request).then((value) => {
      if (current) setPreflight(value);
    }).catch((reason: unknown) => {
      if (current) setPreflightError(reason instanceof Error ? reason.message : "启动前检查失败");
    }).finally(() => { if (current) setPreflightLoading(false); });
    return () => { current = false; };
  }, [step, draft, taskPrompt, inputFiles, selectedSkills, agentModels, agentModelOptions, preflightBlockers]);

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
    setSelectedSkills(null);
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
    uploadControllers.current.forEach((controller, id) => { cancelledUploads.current.add(id); controller.abort(); });
    inputFiles.forEach((item) => {
      if (item.status === "uploaded") void deleteStagedInput(item.id).catch(() => undefined);
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
    draftTouched.current = false; setDraft(defaultDraft()); setInputFiles([]); setPrompt(""); setInstructions(""); setConstraints(""); setSuccessCriteria(""); setDraftSaved(false); setSelectedSkills(null); setAgentModels({}); setSkillDialogOpen(false); setPreflight(null); setPreflightError(""); setError(null); setStep(1);
  }

  async function submit() {
    if (preflightBlockers.length) {
      const blocker = preflightBlockers[0];
      setError(`启动前还需完成：${blocker.message}。`);
      setStep(blocker.step);
      return;
    }
    if (!preflight || preflightLoading || preflightError) { setError("启动前检查尚未通过，请修复问题后重试。"); setStep(5); return; }
    const request: CreateSessionRequest = { ...draft, name: draft.name.trim(), goal: draft.goal.trim(), input: { text: taskPrompt, fileIds: inputFiles.map((item) => item.id) }, selectedSkills, agentModels, preflightFingerprint: preflight.fingerprint };
    setBusy(true); setError(null);
    try { const result = await createTask(request); onCreated(result.task_id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建任务失败"); }
    finally { setBusy(false); }
  }

  return <section className="ref-page new-task-wizard">
    <NewTaskHeader />
    <div className="wizard-body ref-fill">
    <section className="ref-card form-surface">
      <NewTaskProgress step={step} completedSteps={completedSteps} onStep={setStep} />
      {step === 1 ? <TaskGoalStep draft={draft} profiles={profiles} instructions={instructions} constraints={constraints} successCriteria={successCriteria} onDraft={(patch) => { draftTouched.current = true; setDraft((current) => ({ ...current, ...patch })); }} onMode={selectMode} onInstructions={setInstructions} onConstraints={setConstraints} onSuccessCriteria={setSuccessCriteria} /> : null}
      {step === 2 ? <div className="wizard-step-stack"><ModeFields mode={draft.mode} config={draft.modeOptions} setConfig={setConfig} /><fieldset className="span-2 multimodal-step"><legend>任务提示与材料</legend><p className="field-help">补充目标网址、代码片段或附件。附件会归档到独立 Workspace，图片可直接参与多模态分析。</p><MultimodalComposer text={prompt} assets={inputFiles} busy={busy} onText={setPrompt} onFiles={upload} onRemove={removeAsset} /></fieldset></div> : null}
      {step === 3 ? <PolicyFields draft={draft} setPolicy={setPolicy} selectPreset={selectPolicyPreset} /> : null}
      {step === 4 ? <TeamModelStep modeLabel={profile.label} availableMcp={availableMcp.map((item) => item.server).filter((server): server is string => Boolean(server))} skillPreview={skillPreview} skillPreviewLoading={skillPreviewLoading} skillPreviewError={skillPreviewError} selectedSkills={selectedSkills} onSkills={() => void openSkillDialog()} onAutomatic={() => setSelectedSkills(null)} modelOptions={agentModelOptions} modelAssignments={agentModels} modelError={agentModelsError} onModel={(agentId, value) => setAgentModels((current) => ({ ...current, [agentId]: value }))} /> : null}
      {step === 5 ? <fieldset className="span-2 preflight-summary"><legend>第五步：启动前检查</legend><dl className="creation-summary"><dt>场景</dt><dd>{profile.label}</dd><dt>任务</dt><dd>{draft.name || "尚未填写"}</dd><dt>任务目标</dt><dd>{draft.goal || "尚未填写"}</dd><dt>任务说明</dt><dd>{taskPrompt || "无文字说明"}</dd><dt>附件（{inputFiles.length}）</dt><dd>{inputFiles.map((item) => item.originalName).join("；") || "无"}</dd><dt>执行边界</dt><dd>preset={draft.executionPolicy.preset}；network={draft.executionPolicy.network.access}/{draft.executionPolicy.network.interaction}；compute={draft.executionPolicy.local_compute.mode}；high_impact={draft.executionPolicy.high_impact.mode}</dd><dt>Agent 模型</dt><dd>{agentModelOptions?.agents.map((agent) => { const selectedModel = agentModelOptions.models.find((item) => item.provider_id === agentModels[agent.id]?.providerId && item.model_id === agentModels[agent.id]?.modelId); return `${agent.id} → ${selectedModel ? `${selectedModel.provider_name} / ${selectedModel.model_name}` : "未选择"}`; }).join("；") || "未选择"}</dd><dt>预计装配 Skills</dt><dd><SkillPreviewSummary value={skillPreview} loading={skillPreviewLoading} error={skillPreviewError} manual={selectedSkills !== null} onAutomatic={() => setSelectedSkills(null)} /></dd><dt>自动可用 MCP（{availableMcp.length}）</dt><dd>{availableMcp.map((item) => item.server).join(", ") || "当前无已启用且可达/已发现的 MCP 服务"}</dd><dt>完成条件</dt><dd>{successCriteria || `${profile.completion_validator}：${profile.report_sections.join("、") || "证据支持的模式专属验证"}`}</dd><dt>启动前检查</dt><dd><PreflightSummary value={preflight} loading={preflightLoading} error={preflightError} blockers={preflightBlockers} /></dd></dl></fieldset> : null}
      {error ? <p role="alert" className="inline-error span-2">{error}</p> : null}
    </section>
    <NewTaskGuide modeLabel={profile.label} modeDescription={profiles[draft.mode].description} />
    <footer className="wizard-actions"><button type="button" className="secondary-button" disabled={busy} onClick={reset}>取消</button><span className="wizard-save-state" aria-live="polite">{draftSaved ? <><Check size={14} />草稿已保存</> : null}</span><div>{step > 1 ? <button type="button" disabled={busy} onClick={() => setStep((value) => Math.max(1, value - 1))}>上一步</button> : null}<button type="button" className="save-draft-button" disabled={busy} onClick={() => { localStorage.setItem("tga-new-task-draft", JSON.stringify({ draft, instructions, constraints, successCriteria, prompt })); setDraftSaved(true); }}>保存草稿</button>{step < 5 ? <button type="button" disabled={busy} onClick={() => setStep((value) => Math.min(5, value + 1))}>下一步</button> : <button type="button" disabled={busy || preflightLoading || Boolean(preflightError) || (!preflight && !preflightBlockers.length)} onClick={() => void submit()}>{busy ? "处理中..." : preflightLoading ? "正在检查..." : "创建任务并开始"}</button>}</div></footer>
    </div>
    {skillDialogOpen ? <SkillSelectionDialog catalog={skillCatalog} loading={skillCatalogLoading} mode={draft.mode} selected={selectedSkills ?? skillPreview?.skills.map((item) => item.name) ?? []} draft={draft} prompt={taskPrompt} files={inputFiles} onClose={() => setSkillDialogOpen(false)} onApply={(names) => { setSelectedSkills(names); setSkillDialogOpen(false); }} /> : null}
  </section>;

  async function openSkillDialog() {
    setSkillDialogOpen(true);
    if (skillCatalog.length || skillCatalogLoading) return;
    setSkillCatalogLoading(true);
    try {
      setSkillCatalog((await fetchSkillSettings()).skills);
    } catch (reason) {
      setSkillPreviewError(reason instanceof Error ? reason.message : "无法读取 Skill 列表");
    } finally { setSkillCatalogLoading(false); }
  }
}

function TaskGoalStep({ draft, profiles, instructions, constraints, successCriteria, onDraft, onMode, onInstructions, onConstraints, onSuccessCriteria }: { draft: Draft; profiles: Record<TaskMode, ModeProfileContract>; instructions: string; constraints: string; successCriteria: string; onDraft: (patch: Partial<Draft>) => void; onMode: (mode: TaskMode) => void; onInstructions: (value: string) => void; onConstraints: (value: string) => void; onSuccessCriteria: (value: string) => void }) {
  return <div className="task-goal-step">
    <FieldWithCount label="任务名称" required value={draft.name} max={100}><input aria-label="任务名称" value={draft.name} maxLength={100} onChange={(event) => onDraft({ name: event.target.value })} placeholder="请输入任务名称（建议清晰、简洁）" /></FieldWithCount>
    <fieldset className="scene-picker"><legend>模式 <sup>*</sup></legend><div className="mode-card-grid">{TASK_MODES.map((mode) => { const meta = MODE_CARD_META[mode]; const Icon = meta.icon; const selected = draft.mode === mode; return <button type="button" key={mode} className={`mode-card tone-${meta.tone} ${selected ? "selected" : ""}`} aria-pressed={selected} aria-label={`${meta.label}：${meta.description}`} title={profiles[mode].description} onClick={() => onMode(mode)}><span className="mode-card-icon"><Icon size={30} /></span>{selected ? <span className="mode-card-check"><Check size={13} /></span> : null}<strong>{meta.label}</strong><span>{meta.description}</span></button>; })}</div></fieldset>
    <FieldWithCount label="Objective" required info="核心目标与期望结果" value={draft.goal} max={500}><textarea aria-label="Objective" value={draft.goal} maxLength={500} onChange={(event) => onDraft({ goal: event.target.value })} placeholder="请描述本次任务的核心目标与期望达成的结果，例如：发现目标系统中的高危漏洞并获取可复现的利用链。" /></FieldWithCount>
    <FieldWithCount label="Instructions" required info="任务背景、范围和完成要求" value={instructions} max={2000}><textarea aria-label="Instructions" value={instructions} maxLength={2000} onChange={(event) => onInstructions(event.target.value)} placeholder="请提供详细的任务背景、范围、优先级、关注点及完成任务的具体要求。" /></FieldWithCount>
    <FieldWithCount label="Constraints" info="任务边界与限制" value={constraints} max={1000}><textarea aria-label="Constraints" value={constraints} maxLength={1000} onChange={(event) => onConstraints(event.target.value)} placeholder="请列出任务的边界条件与限制，例如：禁止暴力破解、仅在指定时间段扫描、不得影响生产环境等。" /></FieldWithCount>
    <FieldWithCount label="Success Criteria" required info="任务完成的判断标准" value={successCriteria} max={500}><textarea aria-label="Success Criteria" value={successCriteria} maxLength={500} onChange={(event) => onSuccessCriteria(event.target.value)} placeholder="请定义任务完成的判定标准，例如：发现 ≥ 3 个高危漏洞并验证，或完成内网横向移动并获取域管理员权限等。" /></FieldWithCount>
  </div>;
}

function FieldWithCount({ label, required = false, info, value, max, children }: { label: string; required?: boolean; info?: string; value: string; max: number; children: ReactNode }) {
  return <label className="wizard-counted-field"><span><b>{label}{required ? <sup>*</sup> : null}</b>{info ? <small title={info}>i</small> : null}</span>{children}<em>{value.length}/{max}</em></label>;
}

function TeamModelStep({ modeLabel, availableMcp, skillPreview, skillPreviewLoading, skillPreviewError, selectedSkills, onSkills, onAutomatic, modelOptions, modelAssignments, modelError, onModel }: { modeLabel: string; availableMcp: string[]; skillPreview: SkillPreview | null; skillPreviewLoading: boolean; skillPreviewError: string; selectedSkills: string[] | null; onSkills: () => void; onAutomatic: () => void; modelOptions: AgentModelOptions | null; modelAssignments: Record<string, { providerId: string; modelId: string }>; modelError: string; onModel: (agentId: string, value: { providerId: string; modelId: string }) => void }) {
  return <div className="team-model-step">
    <header><span><Users size={18} /></span><div><h2>团队与模型装配</h2><p>系统会根据「{modeLabel}」自动推荐团队、Solver、Skills 与可用工具。</p></div></header>
    <div className="team-model-grid"><article><span>团队模板</span><strong>{modeLabel}标准团队</strong><small>Supervisor 按任务复杂度动态创建 Solver</small></article><article><span>模型策略</span><strong>逐 Agent 独立分配</strong><small>模型与密钥状态会冻结到任务快照</small></article><article><span>编排方式</span><strong>自动匹配</strong><small>支持在运行工作台继续干预和审批</small></article></div>
    <section className="agent-model-assignment"><header><div><Cpu size={17} /><h3>Agent 模型</h3></div><b>{modelOptions?.agents.length ?? 0}</b></header>{modelError ? <p className="team-model-empty"><AlertTriangle size={15} />{modelError} <a href="/settings/models">前往配置</a></p> : <div className="agent-model-grid">{modelOptions?.agents.map((agent) => { const selected = modelAssignments[agent.id]; const value = selected ? `${selected.providerId}:${selected.modelId}` : ""; return <label key={agent.id}><span><strong>{agent.id}</strong><small>{roleLabel(agent.role)}{agent.required ? " · 必需" : " · 按需创建"}</small></span><select aria-label={`${agent.id} 模型`} value={value} onChange={(event) => { const [providerId, modelId] = event.target.value.split(":"); onModel(agent.id, { providerId, modelId }); }}><option value="" disabled>选择已验证模型</option>{modelOptions.models.map((model) => <option key={`${model.provider_id}:${model.model_id}`} value={`${model.provider_id}:${model.model_id}`} disabled={!model.ready}>{model.provider_name} / {model.model_name}{model.ready ? "" : `（${model.verification_status}）`}</option>)}</select></label>; })}</div>}</section>
    <section><header><div><Sparkles size={17} /><h3>Skills</h3></div><button type="button" className="ref-secondary-button" onClick={onSkills}>手动选择</button></header><SkillPreviewSummary value={skillPreview} loading={skillPreviewLoading} error={skillPreviewError} manual={selectedSkills !== null} onAutomatic={onAutomatic} /></section>
    <section><header><div><ShieldCheck size={17} /><h3>自动可用 MCP</h3></div><b>{availableMcp.length}</b></header>{availableMcp.length ? <div className="team-model-chips">{availableMcp.map((server) => <span key={server}>{server}</span>)}</div> : <p className="team-model-empty"><AlertTriangle size={15} />当前没有已启用且可达的 MCP 服务</p>}</section>
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

function SkillPreviewSummary({ value, loading, error, manual, onAutomatic }: { value: SkillPreview | null; loading: boolean; error: string; manual: boolean; onAutomatic: () => void }) {
  if (loading) return <div className="creation-skill-status">正在根据任务内容和可用能力匹配 Skills…</div>;
  if (error) return <div className="creation-skill-status error">预览失败：{error}</div>;
  if (!value?.skills.length && !manual) return <div className="creation-skill-status">当前没有同时满足任务特征和能力要求的 Skill。</div>;
  if (!value?.skills.length) return <div className="creation-skill-preview">
    <div className="creation-skill-mode"><b>手动选择</b><button type="button" className="text-button" onClick={onAutomatic}>恢复自动匹配</button></div>
    <div className="creation-skill-status">已明确选择不装配任何 Skill。</div>
  </div>;
  return <div className="creation-skill-preview">{manual ? <div className="creation-skill-mode"><b>手动选择</b><button type="button" className="text-button" onClick={onAutomatic}>恢复自动匹配</button></div> : <div className="creation-skill-mode"><b>自动匹配</b></div>}{value.skills.map((skill) => <article key={skill.name}>
    <header><b>{skill.name}</b><span>{skill.origin === "custom" ? "用户自定义" : "内置"} · v{skill.version}</span></header>
    <p>{skill.selection_reasons.join("；")}</p>
    <small>{skill.capabilities.length ? `所需能力：${skill.capabilities.join(" · ")}` : "无需额外能力"}</small>
  </article>)}<small>正式创建时会使用同一选择器重新确认并冻结快照。</small></div>;
}

function SkillSelectionDialog({ catalog, loading, mode, selected, draft, prompt, files, onClose, onApply }: { catalog: SkillSetting[]; loading: boolean; mode: TaskMode; selected: string[]; draft: Draft; prompt: string; files: StagedAsset[]; onClose: () => void; onApply: (names: string[]) => void }) {
  const [names, setNames] = useState<string[]>(selected.slice(0, 3));
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const toggle = (name: string) => setNames((current) => current.includes(name) ? current.filter((item) => item !== name) : current.length < 3 ? [...current, name] : current);
  const apply = async () => {
    setChecking(true); setError("");
    try {
      await previewTaskSkills({ mode, goal: draft.goal.trim(), modeOptions: draft.modeOptions, prompt: prompt.trim(), fileNames: files.filter((item) => item.status === "uploaded").map((item) => item.originalName), executionPolicy: draft.executionPolicy, selectedSkills: names });
      onApply(names);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "所选 Skills 无法用于当前任务");
    } finally { setChecking(false); }
  };
  return <div className="dialog-backdrop skill-picker-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="skill-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-picker-title">
    <header><div><span>任务级装配</span><h2 id="skill-picker-title">手动选择 Skills</h2><p>按场景浏览方法手册。当前任务最多装配 3 个，后端会再次校验场景、能力和上下文预算。</p></div><button type="button" className="icon-button" aria-label="关闭 Skill 选择" onClick={onClose}>×</button></header>
    <div className="skill-picker-count"><b>已选择 {names.length}/3</b><small>{names.length ? names.join(" · ") : "明确不装配任何 Skill"}</small></div>
    {error ? <div className="inline-error" role="alert">{error}</div> : null}
    <div className="skill-picker-scenes">{loading ? <div className="skill-loading">正在读取 Skill 注册表…</div> : TASK_MODES.map((scene) => { const skills = catalog.filter((skill) => skill.modes.includes(scene)); return <section key={scene}>
      <header><div><b>{MODE_PROFILES[scene].label}</b><small>{scene}</small></div><span>{skills.length} 个</span></header>
      {skills.length ? <div className="skill-picker-grid">{skills.map((skill) => { const compatible = skill.modes.includes(mode); const checked = names.includes(skill.name); const atLimit = names.length >= 3 && !checked; return <label className={`${checked ? "selected" : ""} ${!compatible ? "incompatible" : ""}`} key={`${scene}:${skill.name}`}>
        <input type="checkbox" aria-label={`${skill.name}（${MODE_PROFILES[scene].label}）`} checked={checked} disabled={!compatible || atLimit || checking} onChange={() => toggle(skill.name)} /><span><strong>{skill.name}</strong><small>{skill.source === "custom" ? "用户自定义" : "内置"} · v{skill.version}</small><p>{skill.summary}</p><em>{compatible ? skill.capabilities.join(" · ") || "无需额外能力" : `不适用于当前${MODE_PROFILES[mode].label}任务`}</em></span>
      </label>; })}</div> : <small>暂无 Skill</small>}
    </section>; })}</div>
    <footer><button type="button" className="secondary-button" disabled={checking} onClick={onClose}>取消</button><button type="button" disabled={checking} onClick={() => void apply()}>{checking ? "正在校验…" : "应用选择"}</button></footer>
  </section></div>;
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
