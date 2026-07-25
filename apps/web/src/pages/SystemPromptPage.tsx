import { FormEvent, useEffect, useState } from "react";
import {
  fetchAgentPromptSettings,
  updateAgentPromptSettings,
  type AgentPromptSettings,
} from "../api/tasks";
import { EmptyState } from "../components/ui/EmptyState";
import type { TaskMode } from "../modes";

const cloneSettings = (value: AgentPromptSettings): AgentPromptSettings => ({
  ...value,
  modes: value.modes.map((mode) => ({ ...mode, methodology: [...mode.methodology] })),
});

export function SystemPromptPage() {
  const [draft, setDraft] = useState<AgentPromptSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    void fetchAgentPromptSettings()
      .then((value) => { setDraft(cloneSettings(value)); setError(""); })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取 System Prompt"))
      .finally(() => setLoading(false));
  }, []);

  const updateMode = (mode: TaskMode, patch: Partial<AgentPromptSettings["modes"][number]>) => {
    if (!draft) return;
    setDraft({ ...draft, modes: draft.modes.map((item) => item.id === mode ? { ...item, ...patch } : item) });
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const saved = await updateAgentPromptSettings(draft);
      setDraft(cloneSettings(saved));
      setMessage("System Prompt 已保存，将用于此后创建的新任务。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "System Prompt 保存失败");
    } finally { setBusy(false); }
  };

  return <section className="page-stack skills-page system-prompt-page">
    <header className="page-title"><div><span className="eyebrow">配置 / 模型指令</span><h1>System Prompt</h1><p>配置所有新任务共用的系统约束，以及每个任务场景的初始系统指令。已创建任务保留创建时快照，不受后续修改影响。</p></div></header>
    {error ? <div className="inline-error" role="alert">{error}</div> : null}
    {message ? <div className="skill-message" role="status">{message}</div> : null}
    <form className="surface prompt-library prompt-editor" onSubmit={save}><div className="surface-head"><div><h2>模型系统指令</h2><p>通用约束在前，当前任务场景的指令随后进入模型的首条 System 消息。</p></div><span className="schema-chip">新任务生效 · 可编辑</span></div>{draft ? <>
      <label className="common-prompt-field"><span>通用系统约束</span><small>所有任务共用，位于场景指令之前。</small><textarea aria-label="通用系统约束" required value={draft.common_system_prompt} onChange={(event) => setDraft({ ...draft, common_system_prompt: event.target.value })} /></label>
      <div className="mode-prompt-stack">{draft.modes.map((mode, index) => <details className="mode-prompt-editor" key={mode.id} open={index === 0}><summary><span>0{index + 1}</span><div><strong>{mode.label}</strong><small>{mode.id}</small></div><b>{mode.methodology.length} 个方法步骤</b></summary><div className="mode-prompt-fields">
        <label>场景名称<input required value={mode.label} onChange={(event) => updateMode(mode.id, { label: event.target.value })} /></label>
        <label>分析方法<small>每行一个步骤，保存时保持有序列表。</small><textarea aria-label={`${mode.label} 分析方法`} required value={mode.methodology.join("\n")} onChange={(event) => updateMode(mode.id, { methodology: promptLines(event.target.value) })} /></label>
        <label>完成重点<textarea aria-label={`${mode.label} 完成重点`} required value={mode.completion_focus} onChange={(event) => updateMode(mode.id, { completion_focus: event.target.value })} /></label>
        <label>观察重点<textarea aria-label={`${mode.label} 观察重点`} required value={mode.observer_focus} onChange={(event) => updateMode(mode.id, { observer_focus: event.target.value })} /></label>
      </div></details>)}</div>
      <div className="prompt-save-row"><p>场景 ID 和配置结构由系统固定，文本内容均可修改。</p><button disabled={busy}>{busy ? "保存中…" : "保存 System Prompt"}</button></div>
    </> : !loading ? <EmptyState label="当前后端未返回 System Prompt。" /> : null}</form>
  </section>;
}

function promptLines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}
