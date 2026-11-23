import {
  type ChangeEvent, type ClipboardEvent, type DragEvent, type ReactNode,
  useEffect, useMemo, useRef, useState,
} from "react";
import { Check, Copy, FileText, History, Image, Paperclip, Play, RotateCcw, Sparkles, Trash2, X } from "lucide-react";
import {
  createTask, deleteStagedInput, fetchAgentModelOptions, fetchKnowledgeSources, fetchTaskCloneConfig, listScenes, stageInput,
  type AgentModelOptions, type KnowledgeSource, type KnowledgeSources, type StagedAsset, type TaskCloneConfig,
} from "../api/tga3-tasks";
import type { ProviderProtocol, SceneConfig } from "../api/tga3-config";
import { ModelPicker } from "../components/ModelPicker";
import { TASK_MODES, type TaskMode } from "../modes";
import {
  clearUnsubmittedDraft,
  formatShortTime,
  loadAllFieldHistory,
  loadLastTaskConfig,
  loadUnsubmittedDraft,
  reconcileAgentModels,
  recordFieldHistory,
  removeFieldHistory,
  saveLastTaskConfig,
  saveUnsubmittedDraft,
  type FieldHistoryEntry,
  type SavedAgentModels,
  type SavedTaskFormSnapshot,
} from "./task-form-memory";

const SCENE_ICONS: Record<TaskMode, string> = {
  penetration_test: "WEB", reverse_engineering: "RE",
  pwn: "PWN", security_misc: "MISC", cryptography: "CRYPTO", ai_security: "AI", aoe: "AOE",
};

export function NewTaskPage({
  onCreated,
  onCancel = () => history.back(),
  cloneFromTaskId = null,
}: {
  onCreated: (id: string) => void;
  onCancel?: () => void;
  cloneFromTaskId?: string | null;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sceneId, setSceneId] = useState<TaskMode>("penetration_test");
  const [aoeMinutes, setAoeMinutes] = useState({ first: "30", second: "15", joint: "20" });
  const [produceReport, setProduceReport] = useState(true);
  const [aoeAutoSubmitFlag, setAoeAutoSubmitFlag] = useState(true);
  const [knowledgeSource, setKnowledgeSource] = useState<KnowledgeSource>("online");
  const [knowledgeSources, setKnowledgeSources] = useState<KnowledgeSources | null>(null);
  const [knowledgeError, setKnowledgeError] = useState("");
  const [scenes, setScenes] = useState<SceneConfig[]>([]);
  const [assets, setAssets] = useState<StagedAsset[]>([]);
  const [agentOptions, setAgentOptions] = useState<AgentModelOptions | null>(null);
  const [agentModels, setAgentModels] = useState<SavedAgentModels>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [infoNotice, setInfoNotice] = useState("");
  const [cloneMeta, setCloneMeta] = useState<TaskCloneConfig | null>(null);
  const [unsubmittedDraft, setUnsubmittedDraft] = useState<SavedTaskFormSnapshot | null>(() =>
    cloneFromTaskId ? null : loadUnsubmittedDraft(),
  );
  const [lastConfig, setLastConfig] = useState<SavedTaskFormSnapshot | null>(() => loadLastTaskConfig());
  const [fieldHistory, setFieldHistory] = useState<Record<string, FieldHistoryEntry[]>>(() => loadAllFieldHistory());
  const [undoField, setUndoField] = useState<{ field: string; previous: string } | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const assetsRef = useRef<StagedAsset[]>([]);
  const pendingModelsRef = useRef<{ sceneId: TaskMode; models: SavedAgentModels; sourceLabel: string } | null>(null);
  const hasUserEditedRef = useRef(false);

  useEffect(() => {
    void listScenes().then((value) => setScenes(value.scenes)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取场景"));
  }, []);
  useEffect(() => {
    void fetchKnowledgeSources().then(setKnowledgeSources).catch(() => setKnowledgeError("无法读取知识库状态，请刷新后重试。"));
  }, []);

  // Load clone template when navigating from "基于此任务新建".
  useEffect(() => {
    if (!cloneFromTaskId) return;
    let active = true;
    setBusy(true);
    setError("");
    void fetchTaskCloneConfig(cloneFromTaskId)
      .then((template) => {
        if (!active) return;
        setCloneMeta(template);
        setName(template.title);
        setDescription(template.prompt);
        setSceneId(template.scene_id);
        setProduceReport(template.produce_report);
        setAoeAutoSubmitFlag(template.aoe_auto_submit_flag);
        setKnowledgeSource(template.knowledge_source);
        setAoeMinutes({
          first: String(template.aoe_budgets.first),
          second: String(template.aoe_budgets.second),
          joint: String(template.aoe_budgets.joint),
        });
        pendingModelsRef.current = {
          sceneId: template.scene_id,
          models: template.agent_models,
          sourceLabel: "原任务",
        };
        setUnsubmittedDraft(null);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "无法加载原任务配置");
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [cloneFromTaskId]);

  useEffect(() => {
    let active = true;
    void fetchAgentModelOptions(sceneId)
      .then((value) => {
        if (!active) return;
        setAgentOptions(value);
        const pending = pendingModelsRef.current;
        if (pending && pending.sceneId === sceneId) {
          pendingModelsRef.current = null;
          const { models, fallbackAgents } = reconcileAgentModels(pending.models, value, modelCompatible);
          setAgentModels(models);
          if (fallbackAgents.length) {
            setInfoNotice(
              `${pending.sourceLabel}中以下 Solver 曾选用的模型已不可用，已自动回退为当前默认模型：${fallbackAgents.join("、")}。`,
            );
          }
        } else {
          setAgentModels(
            Object.fromEntries(
              value.agents.map((agent) => [
                agent.id,
                { provider_id: agent.model.provider_id, model_id: agent.model.model_id, protocol: agent.model.protocol },
              ]),
            ),
          );
        }
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "无法读取 Agent 模型");
      });
    return () => {
      active = false;
    };
  }, [sceneId]);

  // Auto-save unsubmitted content whenever user edits the form.
  useEffect(() => {
    if (!hasUserEditedRef.current) return;
    const timer = window.setTimeout(() => {
      saveUnsubmittedDraft({
        name,
        description,
        sceneId,
        aoeMinutes,
        produceReport,
        aoeAutoSubmitFlag,
        knowledgeSource,
        agentModels,
      });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [name, description, sceneId, aoeMinutes, produceReport, aoeAutoSubmitFlag, knowledgeSource, agentModels]);

  assetsRef.current = assets;
  useEffect(() => () => assetsRef.current.forEach((asset) => { if (asset.previewUrl) URL.revokeObjectURL(asset.previewUrl); }), []);

  function markEdited() {
    hasUserEditedRef.current = true;
  }

  function applySnapshot(snapshot: SavedTaskFormSnapshot, sourceLabel: string) {
    setError("");
    setInfoNotice("");
    setUndoField(null);
    setName(snapshot.name);
    setDescription(snapshot.description);
    setAoeMinutes({ ...snapshot.aoeMinutes });
    setProduceReport(snapshot.produceReport);
    setAoeAutoSubmitFlag(snapshot.aoeAutoSubmitFlag);
    setKnowledgeSource(snapshot.knowledgeSource);
    if (snapshot.sceneId === sceneId && agentOptions) {
      const { models, fallbackAgents } = reconcileAgentModels(snapshot.agentModels, agentOptions, modelCompatible);
      setAgentModels(models);
      setInfoNotice(
        fallbackAgents.length
          ? `已加载${sourceLabel}；以下 Solver 上次选用的模型已不可用，已自动回退为当前默认模型：${fallbackAgents.join("、")}。`
          : `已加载${sourceLabel}。`,
      );
    } else {
      pendingModelsRef.current = { sceneId: snapshot.sceneId, models: snapshot.agentModels, sourceLabel };
      setSceneId(snapshot.sceneId);
      setInfoNotice(`已加载${sourceLabel}。`);
    }
  }

  function restoreUnsubmitted() {
    if (!unsubmittedDraft) return;
    applySnapshot(unsubmittedDraft, "上次未提交的内容");
    setUnsubmittedDraft(null);
    hasUserEditedRef.current = true;
  }

  function dismissUnsubmitted() {
    clearUnsubmittedDraft();
    setUnsubmittedDraft(null);
  }

  function fillLastConfig() {
    if (!lastConfig) return;
    applySnapshot(lastConfig, "上次配置");
    hasUserEditedRef.current = true;
  }

  function applyFieldMemory(fieldKey: string, currentVal: string, nextVal: string, setter: (val: string) => void) {
    if (currentVal.trim() && currentVal !== nextVal) {
      setUndoField({ field: fieldKey, previous: currentVal });
    } else {
      setUndoField(null);
    }
    markEdited();
    setter(nextVal);
  }

  function forgetMemory(fieldKey: string, value: string) {
    setFieldHistory(removeFieldHistory(fieldKey, value));
  }

  const selected = useMemo(() => scenes.find((scene) => scene.id === sceneId), [scenes, sceneId]);
  const validAoeBudget = sceneId !== "aoe" || Object.values(aoeMinutes).every(value =>
    Number.isInteger(Number(value)) && Number(value) >= 1 && Number(value) <= 1440);
  const ready = Boolean(name.trim() && description.trim() && validAoeBudget && scenes.length && agentOptions && agentOptions.agents.every((agent) => {
    const selectedModel = agentModels[agent.id];
    return selectedModel && agentOptions.models.some((model) => model.provider_id === selectedModel.provider_id && model.model_id === selectedModel.model_id && model.protocol === selectedModel.protocol && model.ready && modelCompatible(agent.runtime, model.protocol));
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
    deleteStagedInput(asset.id);
    if (asset.previewUrl) URL.revokeObjectURL(asset.previewUrl);
    setAssets((current) => current.filter((item) => item.id !== asset.id));
  }

  async function submit() {
    if (!ready) { setError("请填写任务名称和描述，并等待附件处理完成。"); return; }
    setBusy(true); setError("");
    try {
      const trimmedTitle = name.trim();
      const trimmedPrompt = description.trim();
      const task = await createTask({
        title: trimmedTitle, sceneId, prompt: trimmedPrompt, knowledgeSource, produceReport,
        fileIds: assets.map((asset) => asset.id), agentModels,
        ...(sceneId === "aoe" ? { aoeAutoSubmitFlag, aoeFirstAttemptMinutes: Number(aoeMinutes.first),
          aoeSecondAttemptMinutes: Number(aoeMinutes.second), aoeJointMinutes: Number(aoeMinutes.joint) } : {}),
      });
      saveLastTaskConfig({
        name: trimmedTitle,
        description: trimmedPrompt,
        sceneId,
        aoeMinutes,
        produceReport,
        aoeAutoSubmitFlag,
        knowledgeSource,
        agentModels,
      });
      recordFieldHistory("title", trimmedTitle);
      recordFieldHistory(`description:${sceneId}`, trimmedPrompt);
      if (sceneId === "aoe") {
        recordFieldHistory("aoe_first", aoeMinutes.first);
        recordFieldHistory("aoe_second", aoeMinutes.second);
        recordFieldHistory("aoe_joint", aoeMinutes.joint);
      }
      setLastConfig(loadLastTaskConfig());
      setFieldHistory(loadAllFieldHistory());
      clearUnsubmittedDraft();
      hasUserEditedRef.current = false;
      onCreated(task.id);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建任务失败"); setBusy(false); }
  }

  function onFiles(event: ChangeEvent<HTMLInputElement>) { void upload(Array.from(event.target.files ?? [])); event.target.value = ""; }
  function onDrop(event: DragEvent<HTMLDivElement>) { event.preventDefault(); void upload(Array.from(event.dataTransfer.files)); }
  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.items).filter((item) => item.kind === "file").map((item) => item.getAsFile()).filter((file): file is File => Boolean(file));
    if (files.length) { event.preventDefault(); void upload(files); }
  }

  const descHistoryKey = `description:${sceneId}`;
  const titleItems = fieldHistory["title"] ?? [];
  const descItems = fieldHistory[descHistoryKey] ?? [];

  return <div className="ref-page workbench-new-task">
    <section className="new-task-modal" aria-labelledby="new-task-title">
      <header className="new-task-modal-head">
        <div>
          <h1 id="new-task-title">创建任务</h1>
          <p>选择场景并提供初始提示，也可以为本任务覆盖 Solver 默认模型。</p>
        </div>
        <div className="new-task-head-actions">
          {lastConfig ? (
            <button type="button" className="ref-secondary-button" disabled={busy} onClick={fillLastConfig}>
              <Sparkles size={14} />一键填充上次配置
            </button>
          ) : null}
          <button className="icon-button" aria-label="关闭" onClick={onCancel}><X size={20} /></button>
        </div>
      </header>
      <div className="new-task-modal-body">
        {cloneMeta ? (
          <div className="new-task-banner is-clone" role="status">
            <Copy size={15} />
            <div>
              <strong>已从任务「{cloneMeta.title}」加载配置</strong>
              <span>本次将创建全新任务并独立保留记录，不影响原任务历史。{cloneMeta.input_files.length ? `原任务包含 ${cloneMeta.input_files.length} 个附件（${cloneMeta.input_files.map((f) => f.name).join("、")}），如需附带请重新上传。` : ""}</span>
            </div>
          </div>
        ) : null}

        {unsubmittedDraft ? (
          <div className="new-task-banner is-draft" role="status">
            <History size={15} />
            <div>
              <strong>检测到上次未提交的内容{formatShortTime(unsubmittedDraft.savedAt) ? `（保存于 ${formatShortTime(unsubmittedDraft.savedAt)}）` : ""}</strong>
              <span>{unsubmittedDraft.name || unsubmittedDraft.description.slice(0, 48)}</span>
            </div>
            <div className="new-task-banner-actions">
              <button type="button" className="ref-primary-button" onClick={restoreUnsubmitted}>点击恢复</button>
              <button type="button" className="ref-secondary-button" onClick={dismissUnsubmitted}>忽略</button>
            </div>
          </div>
        ) : null}

        {infoNotice ? (
          <div className="new-task-banner is-info" role="status">
            <span>{infoNotice}</span>
            <button type="button" className="text-button" onClick={() => setInfoNotice("")}>关闭</button>
          </div>
        ) : null}

        <MemoryField
          className="wide"
          label="任务名称"
          fieldKey="title"
          currentValue={name}
          items={titleItems}
          undoAvailable={undoField?.field === "title"}
          onUndo={() => { if (undoField?.field === "title") { setName(undoField.previous); setUndoField(null); } }}
          onSelect={(val) => applyFieldMemory("title", name, val, setName)}
          onForget={(val) => forgetMemory("title", val)}
        >
          <input
            autoFocus
            maxLength={100}
            value={name}
            onChange={(event) => { markEdited(); setName(event.target.value); }}
            placeholder="例如：JWT 密钥泄露"
          />
        </MemoryField>

        <fieldset className="scene-picker"><legend>题目类型：场景</legend><div className="scene-card-grid">
          {scenes.filter((scene): scene is SceneConfig & { id: TaskMode } => TASK_MODES.includes(scene.id as TaskMode)).map((scene) => <button type="button" key={scene.id} className={sceneId === scene.id ? "selected" : ""} onClick={() => { markEdited(); setSceneId(scene.id); }}><span className="scene-code">{SCENE_ICONS[scene.id]}</span><strong>{scene.name}</strong><small>{scene.description}</small>{sceneId === scene.id ? <Check size={17} /> : null}</button>)}
        </div></fieldset>
        {sceneId === "aoe" ? <fieldset className="wide aoe-budget-field"><legend>单题时间预算</legend>
          <div className="aoe-budget-inputs">{([
            ["first", "第一次尝试（分钟）", "aoe_first"],
            ["second", "第二次尝试（分钟）", "aoe_second"],
            ["joint", "共同攻坚（分钟）", "aoe_joint"],
          ] as const).map(([key, label, histKey]) => (
            <MemoryField
              key={key}
              label={label}
              fieldKey={histKey}
              currentValue={aoeMinutes[key]}
              items={fieldHistory[histKey] ?? []}
              compact
              undoAvailable={undoField?.field === histKey}
              onUndo={() => { if (undoField?.field === histKey) { setAoeMinutes((c) => ({ ...c, [key]: undoField.previous })); setUndoField(null); } }}
              onSelect={(val) => applyFieldMemory(histKey, aoeMinutes[key], val, (next) => setAoeMinutes((c) => ({ ...c, [key]: next })))}
              onForget={(val) => forgetMemory(histKey, val)}
            >
              <input
                type="number"
                min={1}
                max={1440}
                step={1}
                value={aoeMinutes[key]}
                onChange={(event) => { markEdited(); setAoeMinutes((current) => ({ ...current, [key]: event.target.value })); }}
              />
            </MemoryField>
          ))}</div>
          <p>每题分别计时：首次由一位 Worker 尝试，第二次换另一位；共同攻坚由两位共享一份预算。四 Worker 任务只剩一道题时，自动进入围殴，共享共同攻坚预算；正在解题者继续，尝试过的 Worker 恢复原会话。准备时间不计入。</p>
        </fieldset> : null}
        <div className="wide task-run-options">
        {sceneId === "aoe" ? <div className="aoe-submission-option">
          <label>
            <input type="checkbox" checked={aoeAutoSubmitFlag} aria-describedby="aoe-submission-help"
              onChange={(event) => { markEdited(); setAoeAutoSubmitFlag(event.target.checked); }} />
            自动提交 Flag
          </label>
          <small id="aoe-submission-help">取消勾选后，取得 Flag 即可发布最终候选，无需平台成功回执。</small>
        </div> : null}
          <div className="aoe-submission-option">
            <label><input type="checkbox" checked={produceReport} aria-describedby="produce-report-help"
              onChange={(event) => { markEdited(); setProduceReport(event.target.checked); }} />产出报告</label>
            <small id="produce-report-help">勾选后生成报告并整理经验卡；取消勾选后仅整理经验卡。</small>
          </div>
        </div>

        <MemoryField
          className="wide task-description-field"
          label="描述"
          subLabel="用户初始提示词"
          fieldKey={descHistoryKey}
          currentValue={description}
          items={descItems}
          multiline
          undoAvailable={undoField?.field === descHistoryKey}
          onUndo={() => { if (undoField?.field === descHistoryKey) { setDescription(undoField.previous); setUndoField(null); } }}
          onSelect={(val) => applyFieldMemory(descHistoryKey, description, val, setDescription)}
          onForget={(val) => forgetMemory(descHistoryKey, val)}
        >
          <textarea
            rows={7}
            value={description}
            onChange={(event) => { markEdited(); setDescription(event.target.value); }}
            onPaste={onPaste}
            placeholder="粘贴题目描述、授权目标、已知信息和期望结果；也可以直接粘贴图片。"
          />
        </MemoryField>

        <div className="task-attachment-zone" onDragOver={(event) => event.preventDefault()} onDrop={onDrop}><input ref={inputRef} type="file" multiple hidden onChange={onFiles} /><button type="button" className="ref-secondary-button" onClick={() => inputRef.current?.click()}><Paperclip size={16} />添加文件或图片</button><span>支持拖放、文件选择以及在描述框粘贴图片</span></div>
        {assets.length ? <div className="task-attachment-list">{assets.map((asset) => <article key={asset.id}>{asset.previewUrl ? <img src={asset.previewUrl} alt="" /> : asset.mediaKind === "image" ? <Image size={22} /> : <FileText size={22} />}<span><strong>{asset.originalName}</strong><small>{formatBytes(asset.size)}</small></span><button aria-label={`移除 ${asset.originalName}`} onClick={() => void remove(asset)}><X size={15} /></button></article>)}</div> : null}
        <label className="wide knowledge-source-field">Worker 知识库
          <select aria-label="Worker 知识库" value={knowledgeSource} onChange={(event) => { markEdited(); setKnowledgeSource(event.target.value as KnowledgeSource); }}>
            <option value="online" disabled={knowledgeSources?.online.available === false}>在线知识库 · ctf42</option>
            <option value="local" disabled={!knowledgeSources?.local.available}>本地知识库 · CTF Wiki{knowledgeSources && !knowledgeSources.local.available ? "（未就绪）" : ""}</option>
          </select>
          <small>{knowledgeError || (!knowledgeSources ? "正在检查知识库…" : knowledgeSource === "local"
            ? `本地已索引 ${knowledgeSources.local.documents} 篇文档，检索与正文读取无需联网。`
            : "在线检索技法、历史题目及 Writeup 链接，需要网络连接。")}</small>
          {knowledgeSources && !knowledgeSources.local.available ? <small>本地 CTF Wiki 尚未就绪，请先运行 tga3 sync-knowledge。</small> : null}
          {knowledgeSources && !knowledgeSources.online.available && knowledgeSource === "online" ? <small role="alert">在线知识库已禁用，请选择已就绪的本地知识库。</small> : null}
        </label>
        {agentOptions ? <fieldset className="task-agent-models"><legend>本任务 Solver 模型</legend><p>默认继承 Solver 配置；这里的选择只影响本任务。</p><div>{agentOptions.agents.map((agent) => {
          const compatible = agentOptions.models.filter((model) => modelCompatible(agent.runtime, model.protocol));
          const current = agentModels[agent.id];
          return <div className="task-agent-model-row" key={agent.id}><span><strong>{agent.display_name}</strong><small>{agent.id}</small></span>
            <ModelPicker label={`${agent.display_name} 模型`} options={compatible} value={current}
              onChange={(model) => { markEdited(); setAgentModels((value) => ({ ...value, [agent.id]: model })); }} />
          </div>;
        })}</div></fieldset> : null}
        <aside className="new-task-config-note"><strong>{sceneId === "aoe" ? produceReport ? "收集题目 → 分题解题 → 逐题报告" : "收集题目 → 分题解题 → 经验卡" : "Scout → Workers → Reporter"}</strong><p>{sceneId === "aoe" ? "四个 Worker 优先领取未尝试的简单题，题目各自保留黑板和文件，按所选选项整理产出。" : `${selected?.name ?? "场景"}提示词、题目描述和附件索引将自动进入黑板。`}</p></aside>
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
      </div>
      <footer className="new-task-modal-actions"><button type="button" className="ref-secondary-button" disabled={busy} onClick={onCancel}>取消</button><button type="button" className="ref-primary-button" disabled={!ready || !knowledgeSources?.[knowledgeSource].available} onClick={() => void submit()}><Play size={16} />{busy ? "创建中…" : "创建并启动"}</button></footer>
    </section>
  </div>;
}

function MemoryField({
  className = "",
  label,
  subLabel,
  fieldKey,
  currentValue,
  items,
  multiline = false,
  compact = false,
  undoAvailable = false,
  onUndo,
  onSelect,
  onForget,
  children,
}: {
  className?: string;
  label: string;
  subLabel?: string;
  fieldKey: string;
  currentValue: string;
  items: FieldHistoryEntry[];
  multiline?: boolean;
  compact?: boolean;
  undoAvailable?: boolean;
  onUndo: () => void;
  onSelect: (value: string) => void;
  onForget: (value: string) => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const closeTimerRef = useRef<number | null>(null);
  const suggestions = items.filter((item) => item.value !== currentValue);

  function cancelClose() {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }

  function openNow() {
    cancelClose();
    if (suggestions.length > 0) setOpen(true);
  }

  function scheduleClose() {
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => setOpen(false), 180);
  }

  useEffect(() => () => cancelClose(), []);

  const willReplace = Boolean(currentValue.trim());

  return (
    <div
      className={`memory-field-wrap${compact ? " is-compact" : ""} ${className}`.trim()}
      onMouseEnter={openNow}
      onMouseLeave={scheduleClose}
      data-field={fieldKey}
    >
      <label className={className}>
        <span className="memory-field-label-row">
          <span>{label}{subLabel ? <span className="memory-field-sub">{subLabel}</span> : null}</span>
        </span>
        {children}
      </label>
      {undoAvailable ? (
        <button type="button" className="text-button memory-undo-btn" onClick={onUndo}>
          <RotateCcw size={12} />撤销填入
        </button>
      ) : null}

      {open && suggestions.length > 0 ? (
        <div
          className={`memory-popover${multiline ? " is-multiline" : ""}`}
          role="region"
          aria-label={`${label} 历史记忆`}
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
        >
          <header>
            <span><History size={12} /> 上次填写{willReplace ? "（点击将替换当前内容）" : "（点击快速填入）"}</span>
          </header>
          <ul>
            {suggestions.map((entry, idx) => (
              <li key={`${entry.savedAt}-${idx}`}>
                <button
                  type="button"
                  className="memory-option-btn"
                  onClick={() => {
                    onSelect(entry.value);
                    setOpen(false);
                  }}
                >
                  <span className="memory-option-text">
                    {multiline && entry.value.length > 140 ? `${entry.value.slice(0, 140)}…` : entry.value}
                  </span>
                  <small>
                    {idx === 0 ? "最近一次" : `较早记录`}
                    {multiline ? ` · ${entry.value.length} 字` : ""}
                    {formatShortTime(entry.savedAt) ? ` · ${formatShortTime(entry.savedAt)}` : ""}
                  </small>
                </button>
                <button
                  type="button"
                  className="memory-forget-btn"
                  aria-label={`清除这条${label}记忆`}
                  title="清除此条记忆"
                  onClick={(event) => {
                    event.stopPropagation();
                    onForget(entry.value);
                  }}
                >
                  <Trash2 size={12} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function formatBytes(value: number) { return value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`; }
function modelCompatible(runtime: "codex_cli" | "claude_cli" | "opencode_cli" | "qwen_cli", protocol: "openai_responses" | "openai_chat_completions" | "anthropic") { return runtime === "claude_cli" ? protocol === "anthropic" : runtime === "codex_cli" ? protocol === "openai_responses" : true; }
