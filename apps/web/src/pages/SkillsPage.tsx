import { ChangeEvent, DragEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteSkill,
  fetchAgentPromptSettings,
  fetchSkillDetail,
  fetchSkillSettings,
  importSkill,
  updateSkill,
  updateAgentPromptSettings,
  type AgentPromptSettings,
  type SkillDetail,
  type SkillSetting,
} from "../api/tasks";
import { EmptyState } from "../components/ui/EmptyState";
import { MODE_PROFILES, TASK_MODES, type TaskMode } from "../modes";

type SkillDraft = Pick<SkillDetail, "modes" | "capabilities" | "tags" | "version" | "body">;

function clonePromptSettings(value: AgentPromptSettings): AgentPromptSettings {
  return { ...value, modes: value.modes.map((mode) => ({ ...mode, methodology: [...mode.methodology] })) };
}

export function SkillsPage() {
  const [skills, setSkills] = useState<SkillSetting[]>([]);
  const [promptDraft, setPromptDraft] = useState<AgentPromptSettings | null>(null);
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const [draft, setDraft] = useState<SkillDraft | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState<TaskMode | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadSceneRef = useRef<TaskMode | null>(null);
  const grouped = useMemo(() => Object.fromEntries(TASK_MODES.map((mode) => [mode, skills.filter((skill) => skill.modes.includes(mode))])) as Record<TaskMode, SkillSetting[]>, [skills]);

  const load = async () => {
    setLoading(true);
    try {
      const [skillData, promptData] = await Promise.all([fetchSkillSettings(), fetchAgentPromptSettings()]);
      setSkills(skillData.skills);
      setPromptDraft(clonePromptSettings(promptData));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 Skills 与模型系统指令");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  const openSkill = async (skill: SkillSetting) => {
    setBusy(true); setError("");
    try {
      const detail = (await fetchSkillDetail(skill.name)).skill;
      setSelected(detail);
      setDraft(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 Skill");
    } finally { setBusy(false); }
  };
  const upload = async (file: File, scene: TaskMode) => {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await importSkill(file, scene);
      setMessage(`${result.skill.name} 已导入到“${MODE_PROFILES[scene].label}”，并会按场景自动参与后续任务。`);
      await load();
      setSelected(result.skill);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Skill 上传失败");
    } finally {
      setBusy(false);
      uploadSceneRef.current = null;
      if (inputRef.current) inputRef.current.value = "";
    }
  };
  const choose = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    const scene = uploadSceneRef.current;
    if (file && scene) void upload(file, scene);
  };
  const selectForScene = (scene: TaskMode) => {
    if (busy) return;
    uploadSceneRef.current = scene;
    inputRef.current?.click();
  };
  const drop = (event: DragEvent<HTMLDivElement>, scene: TaskMode) => {
    event.preventDefault();
    setDragging(null);
    const file = event.dataTransfer.files?.[0];
    if (file && !busy) void upload(file, scene);
  };
  const beginEdit = () => {
    if (!selected) return;
    setDraft({ modes: selected.modes, capabilities: selected.capabilities, tags: selected.tags, version: selected.version, body: selected.body });
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected || !draft) return;
    setBusy(true); setError("");
    try {
      const result = await updateSkill(selected.name, draft);
      setSelected(result.skill); setDraft(null); setMessage(`${selected.name} 已更新。`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Skill 保存失败"); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      await deleteSkill(selected.name);
      setMessage(`${selected.name} 已删除。`); setSelected(null); setDraft(null); setConfirmDelete(false);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Skill 删除失败"); }
    finally { setBusy(false); }
  };
  const savePrompts = async (event: FormEvent) => {
    event.preventDefault();
    if (!promptDraft) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const saved = await updateAgentPromptSettings(promptDraft);
      setPromptDraft(clonePromptSettings(saved));
      setMessage("模型系统指令已保存，将用于此后创建的新任务。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模型系统指令保存失败");
    } finally { setBusy(false); }
  };
  const updateModePrompt = (mode: TaskMode, patch: Partial<AgentPromptSettings["modes"][number]>) => {
    if (!promptDraft) return;
    setPromptDraft({ ...promptDraft, modes: promptDraft.modes.map((item) => item.id === mode ? { ...item, ...patch } : item) });
  };

  return <section className="page-stack skills-page">
    <header className="page-title"><div><span className="eyebrow">配置 / 方法与指令</span><h1>Skills 与模型指令</h1><p>Skills 按任务场景自动装配方法手册；模型指令用于配置新任务实际收到的通用系统约束和场景初始系统指令。</p></div><details className="skill-format-guide"><summary>Skill 文件格式</summary><pre>{`---\nname: my-scene-playbook\nversion: "1"\nmodes: [penetration_test]\ncapabilities: [http.request, artifact.inspect]\ntags: [web, auth]\n---\n# 使用时机\n说明该 Skill 适用的证据条件。\n\n# 工作流\n给出可验证、可停止的步骤。`}</pre></details></header>
    {error ? <div className="inline-error" role="alert">{error}</div> : null}
    {message ? <div className="skill-message" role="status">{message}</div> : null}
    <input ref={inputRef} hidden type="file" accept=".md,text/markdown" onChange={choose} />
    <div className="scene-skill-stack">{TASK_MODES.map((mode, index) => <section className="scene-skill-section" key={mode}>
      <header><div><span>0{index + 1}</span><div><h2>{MODE_PROFILES[mode].label}</h2><p>{MODE_PROFILES[mode].description}</p></div></div><b>{grouped[mode].length} 个 Skill</b></header>
      <div className={`skill-drop-zone ${dragging === mode ? "active" : ""} ${busy ? "busy" : ""}`} role="button" tabIndex={0} aria-label={`上传到 ${MODE_PROFILES[mode].label}`} onClick={() => selectForScene(mode)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") selectForScene(mode); }} onDragOver={(event) => { event.preventDefault(); setDragging(mode); }} onDragLeave={() => setDragging(null)} onDrop={(event) => drop(event, mode)}>
        <span>MD</span><div><strong>{busy ? "正在处理 Skill…" : `拖拽 ${MODE_PROFILES[mode].label} Skill 到这里`}</strong><small>Markdown 中的 modes 必须包含 <code>{mode}</code>，最大 512 KB。</small></div><b>选择文件</b>
      </div>
      {grouped[mode].length ? <div className="skill-card-grid">{grouped[mode].map((skill) => <button className="skill-card" key={`${mode}:${skill.name}`} onClick={() => void openSkill(skill)} disabled={busy}>
        <div><span className={`skill-origin ${skill.source}`}>{skill.source === "custom" ? "用户自定义" : "内置"}</span><small>v{skill.version}</small></div><h3>{skill.name}</h3><p>{skill.summary}</p><footer>{skill.tags.slice(0, 4).map((tag) => <span key={tag}>#{tag}</span>)}<em>查看详情 ›</em></footer>
      </button>)}</div> : <EmptyState label="此场景暂无 Skill，可上传自定义 Markdown。" />}
    </section>)}</div>
    <form className="surface prompt-library prompt-editor" onSubmit={savePrompts}><div className="surface-head"><div><h2>模型系统指令</h2><p>这里保存的内容会进入新任务的 System Prompt。已创建任务保留创建时的指令快照，不受后续修改影响。</p></div><span className="schema-chip">新任务生效 · 可编辑</span></div>{promptDraft ? <>
      <label className="common-prompt-field"><span>通用系统约束</span><small>所有任务共用，位于场景指令之前。</small><textarea aria-label="通用系统约束" required value={promptDraft.common_system_prompt} onChange={(event) => setPromptDraft({ ...promptDraft, common_system_prompt: event.target.value })} /></label>
      <div className="mode-prompt-stack">{promptDraft.modes.map((mode, index) => <details className="mode-prompt-editor" key={mode.id} open={index === 0}><summary><span>0{index + 1}</span><div><strong>{mode.label}</strong><small>{mode.id}</small></div><b>{mode.methodology.length} 个方法步骤</b></summary><div className="mode-prompt-fields">
        <label>场景名称<input required value={mode.label} onChange={(event) => updateModePrompt(mode.id, { label: event.target.value })} /></label>
        <label>分析方法<small>每行一个步骤，保存时保持有序列表。</small><textarea aria-label={`${mode.label} 分析方法`} required value={mode.methodology.join("\n")} onChange={(event) => updateModePrompt(mode.id, { methodology: promptLines(event.target.value) })} /></label>
        <label>完成重点<textarea aria-label={`${mode.label} 完成重点`} required value={mode.completion_focus} onChange={(event) => updateModePrompt(mode.id, { completion_focus: event.target.value })} /></label>
        <label>观察重点<textarea aria-label={`${mode.label} 观察重点`} required value={mode.observer_focus} onChange={(event) => updateModePrompt(mode.id, { observer_focus: event.target.value })} /></label>
      </div></details>)}</div>
      <div className="prompt-save-row"><p>场景 ID 和配置结构由系统固定，文本内容均可修改。</p><button disabled={busy}>{busy ? "保存中…" : "保存模型指令"}</button></div>
    </> : !loading ? <EmptyState label="当前后端未返回模型系统指令。" /> : null}</form>
    {selected ? <div className="dialog-backdrop skill-dialog-backdrop" role="presentation"><section className="skill-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-dialog-title">
      <header><div><span className={`skill-origin ${selected.source}`}>{selected.source === "custom" ? "用户自定义" : "内置"}</span><h2 id="skill-dialog-title">{selected.name}</h2><p>{selected.modes.map((mode) => MODE_PROFILES[mode].label).join(" · ")}</p></div><button className="icon-button" aria-label="关闭 Skill" onClick={() => { setSelected(null); setDraft(null); }}>×</button></header>
      {draft ? <SkillEditor draft={draft} setDraft={setDraft} busy={busy} onSubmit={save} onCancel={() => setDraft(null)} /> : <><div className="skill-detail-meta"><span>版本 <b>{selected.version}</b></span><span>能力 <b>{selected.capabilities.join(" · ") || "无"}</b></span><span>标签 <b>{selected.tags.join(" · ") || "无"}</b></span></div><article className="skill-markdown"><pre>{selected.body}</pre></article><footer><button className="secondary-button" onClick={() => { setSelected(null); setDraft(null); }}>关闭</button><button className="secondary-button" onClick={beginEdit}>修改</button><button className="danger-button" onClick={() => setConfirmDelete(true)}>删除</button></footer></>}
      {confirmDelete ? <div className="skill-delete-confirm"><p>确定删除自定义 Skill “{selected.name}”？后续任务将不再加载它。</p><div><button className="secondary-button" disabled={busy} onClick={() => setConfirmDelete(false)}>返回</button><button className="danger-button" disabled={busy} onClick={() => void remove()}>{busy ? "删除中…" : "确认删除"}</button></div></div> : null}
    </section></div> : null}
    {loading && !skills.length ? <div className="skill-loading">正在读取 Skill 注册表…</div> : null}
  </section>;
}

function SkillEditor({ draft, setDraft, busy, onSubmit, onCancel }: { draft: SkillDraft; setDraft: (value: SkillDraft) => void; busy: boolean; onSubmit: (event: FormEvent) => void; onCancel: () => void }) {
  const toggleMode = (mode: TaskMode) => setDraft({ ...draft, modes: draft.modes.includes(mode) ? draft.modes.filter((item) => item !== mode) : [...draft.modes, mode] });
  return <form className="skill-editor" onSubmit={onSubmit}><fieldset><legend>适用场景</legend><div className="skill-mode-checks">{TASK_MODES.map((mode) => <label key={mode}><input type="checkbox" checked={draft.modes.includes(mode)} onChange={() => toggleMode(mode)} />{MODE_PROFILES[mode].label}</label>)}</div></fieldset><div className="skill-editor-fields"><label>版本<input value={draft.version} onChange={(event) => setDraft({ ...draft, version: event.target.value })} /></label><label>能力（逗号分隔）<input value={draft.capabilities.join(", ")} onChange={(event) => setDraft({ ...draft, capabilities: tokens(event.target.value) })} /></label><label>标签（逗号分隔）<input value={draft.tags.join(", ")} onChange={(event) => setDraft({ ...draft, tags: tokens(event.target.value) })} /></label></div><label>Skill 正文<textarea value={draft.body} onChange={(event) => setDraft({ ...draft, body: event.target.value })} /></label><footer><button type="button" className="secondary-button" disabled={busy} onClick={onCancel}>取消</button><button disabled={busy || !draft.modes.length || !draft.body.trim()}>{busy ? "保存中…" : "保存修改"}</button></footer></form>;
}

function tokens(value: string): string[] { return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean); }
function promptLines(value: string): string[] { return value.split("\n").map((item) => item.trim()).filter(Boolean); }
