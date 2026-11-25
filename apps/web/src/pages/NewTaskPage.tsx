import {
  type ChangeEvent, type ClipboardEvent, type DragEvent,
  useEffect, useMemo, useRef, useState,
} from "react";
import { Check, Copy, FileText, History, Image, Paperclip, Play, Sparkles, X } from "lucide-react";
import {
  ALL_RUNTIMES,
  RUNTIME_SHORT_LABELS,
  modelCompatible,
  type WorkerRuntime,
} from "../api/model-compatibility";
import {
  createTask, deleteStagedInput, fetchAgentModelOptions, fetchKnowledgeSources, fetchTaskCloneConfig, listScenes, stageInput,
  type AgentModelOptions, type KnowledgeSource, type KnowledgeSources, type StagedAsset, type TaskCloneConfig,
} from "../api/tga3-tasks";
import type { SceneConfig } from "../api/tga3-config";
import { ModelPicker } from "../components/ModelPicker";
import { MemoryField } from "../components/ui/FieldMemoryPopover";
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
  const initialQuery = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
  const queryScene = initialQuery?.get("scene");
  const initialScene: TaskMode =
    queryScene && TASK_MODES.includes(queryScene as TaskMode) ? (queryScene as TaskMode) : "penetration_test";
  const [name, setName] = useState(() => initialQuery?.get("title") ?? "");
  const [description, setDescription] = useState(() => initialQuery?.get("prompt") ?? "");
  const [sceneId, setSceneId] = useState<TaskMode>(initialScene);
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
    const effectiveRuntime = selectedModel?.runtime ?? agent.runtime;
    return selectedModel && agentOptions.models.some((model) => model.provider_id === selectedModel.provider_id && model.model_id === selectedModel.model_id && model.protocol === selectedModel.protocol && model.ready && modelCompatible(effectiveRuntime, model.protocol));
  }) && !busy && assets.every((asset) => asset.status === "uploaded"));

  function switchTaskRuntime(agentId: string, defaultRuntime: WorkerRuntime, nextRuntime: WorkerRuntime) {
    if (!agentOptions) return;
    markEdited();
    setAgentModels((current) => {
      const existing = current[agentId];
      const compatible = agentOptions.models.filter(
        (model) => model.ready && modelCompatible(nextRuntime, model.protocol),
      );
      const keepExisting =
        existing &&
        compatible.some(
          (model) =>
            model.provider_id === existing.provider_id &&
            model.model_id === existing.model_id &&
            model.protocol === existing.protocol,
        );
      const chosen = keepExisting
        ? existing
        : compatible[0]
          ? {
              provider_id: compatible[0].provider_id,
              model_id: compatible[0].model_id,
              protocol: compatible[0].protocol,
            }
          : existing
            ? { provider_id: "", model_id: "", protocol: existing.protocol }
            : { provider_id: "", model_id: "", protocol: "openai_responses" as const };
      const nextEntry =
        nextRuntime === defaultRuntime
          ? { provider_id: chosen.provider_id, model_id: chosen.model_id, protocol: chosen.protocol }
          : {
              provider_id: chosen.provider_id,
              model_id: chosen.model_id,
              protocol: chosen.protocol,
              runtime: nextRuntime,
            };
      return { ...current, [agentId]: nextEntry };
    });
  }

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

  return <div className="ref-page launch-shell workbench-new-task">
    <section className="page-frame is-md" aria-labelledby="new-task-title">
      <header className="proto-section-bar">
        <div style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
          <h1 id="new-task-title" className="proto-section-title" style={{ margin: 0 }}>创建任务</h1>
          <span className="mono subtle" style={{ fontSize: "11px" }}>NEW OPERATION · 两层聚焦发射台</span>
        </div>
        <div className="button-row">
          {lastConfig ? (
            <button type="button" className="ref-secondary-button" style={{ height: 24, fontSize: "11px" }} disabled={busy} onClick={fillLastConfig}>
              <Sparkles size={12} />一键填充上次配置
            </button>
          ) : null}
          <button type="button" className="text-button" aria-label="关闭" onClick={onCancel}><X size={16} /></button>
        </div>
      </header>

      <div className="launch-grid">
        <div className="launch-main">
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

        <fieldset className="launch-scene-fieldset">
          <legend>题目类型：场景</legend>
          <div className="scene-seg-bar">
            {scenes.filter((scene): scene is SceneConfig & { id: TaskMode } => TASK_MODES.includes(scene.id as TaskMode)).map((scene) => (
              <button
                type="button"
                key={scene.id}
                className={`scene-seg-btn${sceneId === scene.id ? " selected" : ""}`}
                onClick={() => { markEdited(); setSceneId(scene.id); }}
              >
                <span className="scene-code">{SCENE_ICONS[scene.id]}</span>
                <strong>{scene.name}</strong>
              </button>
            ))}
          </div>
          {selected ? <p className="scene-seg-hint">{selected.description}</p> : null}
        </fieldset>

        <div className="unified-editor">
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
              placeholder="输入任务名称（例如：JWT 密钥泄露）"
            />
          </MemoryField>

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
              placeholder="粘贴题目描述、授权目标、已知信息和期望结果；也可以直接 Ctrl+V 粘贴图片。"
            />
          </MemoryField>

          <div className="ue-attach-bar" onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
              <input ref={inputRef} type="file" multiple hidden onChange={onFiles} />
              <button type="button" className="ref-secondary-button" style={{ height: 24, fontSize: "11px" }} onClick={() => inputRef.current?.click()}>
                <Paperclip size={13} />添加文件或图片
              </button>
              {assets.length ? assets.map((asset) => (
                <span key={asset.id} className="ref-chip">
                  {asset.mediaKind === "image" ? <Image size={11} /> : <FileText size={11} />}
                  <span>{asset.originalName} ({formatBytes(asset.size)})</span>
                  <button type="button" aria-label={`移除 ${asset.originalName}`} onClick={() => void remove(asset)} style={{ border: "none", background: "none", padding: 0, display: "inline-flex" }}>
                    <X size={11} />
                  </button>
                </span>
              )) : <span className="mono subtle" style={{ fontSize: "11px" }}>支持拖放文件或在描述框粘贴截图</span>}
            </div>
          </div>
        </div>

        {sceneId === "aoe" ? <fieldset className="wide aoe-budget-field aoe-budget-strip"><legend>单题时间预算</legend>
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
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        </div>

        <aside className="launch-side">
          <div className="proto-section-bar">
            <span className="proto-section-title">FLEET & STRATEGY · 舰队与策略</span>
            <span className="mono subtle" style={{ fontSize: "10px" }}>{sceneId === "aoe" ? "AOE" : "SINGLE"}</span>
          </div>
          <div className="launch-side-scroll">
            {agentOptions ? <fieldset className="launch-rack-fieldset task-agent-models"><legend>本任务 Solver 装配与模型</legend><div>{agentOptions.agents.map((agent) => {
              const current = agentModels[agent.id];
              const effectiveRuntime = current?.runtime ?? agent.runtime;
              const compatible = agentOptions.models.filter((model) => modelCompatible(effectiveRuntime, model.protocol));
              return <div className="rack-item" key={agent.id}>
                <div className="rack-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <strong style={{ fontSize: "12px" }}>{agent.display_name}</strong>
                      <small className="mono subtle" style={{ fontSize: "10px" }}>{agent.id}</small>
                    </span>
                    <div className="task-agent-cli-pills" role="group" aria-label={`${agent.display_name} CLI 引擎`}>
                      {ALL_RUNTIMES.map((rt) => (
                        <button
                          key={rt}
                          type="button"
                          className={`task-agent-cli-pill${effectiveRuntime === rt ? " is-selected" : ""}`}
                          aria-pressed={effectiveRuntime === rt}
                          disabled={busy}
                          onClick={() => switchTaskRuntime(agent.id, agent.runtime, rt)}
                        >
                          {RUNTIME_SHORT_LABELS[rt]}
                        </button>
                      ))}
                    </div>
                  </div>
                  <ModelPicker label={`${agent.display_name} 模型`} options={compatible} value={current?.model_id ? current : undefined}
                    onChange={(model) => {
                      markEdited();
                      setAgentModels((value) => ({
                        ...value,
                        [agent.id]: effectiveRuntime === agent.runtime ? model : { ...model, runtime: effectiveRuntime },
                      }));
                    }} />
                </div>
              </div>;
            })}</div></fieldset> : null}

            <div className="launch-side-options">
              <label className="wide knowledge-source-field" style={{ display: "grid", gap: 4, fontSize: "12px" }}>
                <span className="caps mono subtle">Worker 知识库</span>
                <select aria-label="Worker 知识库" value={knowledgeSource} onChange={(event) => { markEdited(); setKnowledgeSource(event.target.value as KnowledgeSource); }} style={{ height: 28, padding: "0 8px", border: "1px solid var(--c-border)", borderRadius: "var(--r-sm)", background: "var(--c-input-bg)" }}>
                  <option value="online" disabled={knowledgeSources?.online.available === false}>在线知识库 · ctf42</option>
                  <option value="local" disabled={!knowledgeSources?.local.available}>本地知识库 · CTF Wiki{knowledgeSources && !knowledgeSources.local.available ? "（未就绪）" : ""}</option>
                </select>
                <small className="subtle" style={{ fontSize: "11px" }}>{knowledgeError || (!knowledgeSources ? "正在检查知识库…" : knowledgeSource === "local"
                  ? `本地已索引 ${knowledgeSources.local.documents} 篇文档，检索与正文读取无需联网。`
                  : "在线检索技法、历史题目及 Writeup 链接，需要网络连接。")}</small>
                {knowledgeSources && !knowledgeSources.local.available ? <small className="subtle">本地 CTF Wiki 尚未就绪，请先运行 tga3 sync-knowledge。</small> : null}
                {knowledgeSources && !knowledgeSources.online.available && knowledgeSource === "online" ? <small role="alert">在线知识库已禁用，请选择已就绪的本地知识库。</small> : null}
              </label>

              <div className="wide task-run-options" style={{ display: "grid", gap: 8 }}>
                {sceneId === "aoe" ? <div className="aoe-submission-option">
                  <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "12px" }}>
                    <input type="checkbox" checked={aoeAutoSubmitFlag} aria-describedby="aoe-submission-help"
                      onChange={(event) => { markEdited(); setAoeAutoSubmitFlag(event.target.checked); }} />
                    自动提交 Flag
                  </label>
                  <small id="aoe-submission-help" className="subtle" style={{ fontSize: "11px" }}>取消勾选后，取得 Flag 即可发布最终候选，无需平台成功回执。</small>
                </div> : null}
                <div className="aoe-submission-option">
                  <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "12px" }}>
                    <input type="checkbox" checked={produceReport} aria-describedby="produce-report-help"
                      onChange={(event) => { markEdited(); setProduceReport(event.target.checked); }} />
                    产出报告
                  </label>
                  <small id="produce-report-help" className="subtle" style={{ fontSize: "11px" }}>勾选后生成报告并整理经验卡；取消勾选后仅整理经验卡。</small>
                </div>
              </div>
            </div>
          </div>

          <footer className="launch-side-foot">
            <button type="button" className="ref-secondary-button" disabled={busy} onClick={onCancel}>取消</button>
            <button type="button" className="ref-primary-button" disabled={!ready || !knowledgeSources?.[knowledgeSource].available} onClick={() => void submit()}>
              <Play size={14} />{busy ? "创建中…" : "创建并启动"}
            </button>
          </footer>
        </aside>
      </div>
    </section>
  </div>;
}

function formatBytes(value: number) { return value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`; }
