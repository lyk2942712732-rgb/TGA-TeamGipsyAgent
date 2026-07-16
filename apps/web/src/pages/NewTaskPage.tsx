import { FormEvent, useState } from "react";
import { createTask, type TGATask } from "../api/tasks";

/** HTTP deployments do not always expose crypto.randomUUID. */
export function newTaskId(): string {
  const uuid = globalThis.crypto?.randomUUID;
  if (typeof uuid === "function") {
    return `task_${uuid.call(globalThis.crypto).replace(/-/g, "").slice(0, 12)}`;
  }
  const values = new Uint32Array(2);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(values);
    return `task_${Array.from(values, (value) => value.toString(16).padStart(8, "0")).join("").slice(0, 12)}`;
  }
  return `task_${`${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`.slice(0, 12).padEnd(12, "0")}`;
}

const defaultTask = (): TGATask => ({
  id: newTaskId(),
  name: "新建安全任务",
  mode: "ctf",
  target: "http://127.0.0.1:8080",
  target_theme: "",
  target_description: "",
  goal: "分析目标并持续使用工具，直到完成任务。",
  flag_format: "[A-Za-z0-9_]{2,32}\\{[^{}\\s]{4,200}\\}",
});

export function NewTaskPage({ onCreated }: { onCreated: (id: string) => void }) {
  const [task, setTask] = useState(defaultTask);
  const [hint, setHint] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof TGATask>(key: K, value: TGATask[K]) =>
    setTask((current) => ({ ...current, [key]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!task.name.trim() || !task.target.trim() || !task.goal.trim()) {
      setError("请填写名称、目标和任务目标。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await createTask({ ...task, target: task.target.trim() }, hint.trim() || undefined);
      onCreated(result.task_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建 Session 失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page-stack">
      <header className="page-title">
        <div>
          <span className="eyebrow">Sessions / New</span>
          <h1>新建 Session</h1>
          <p>填写目标与任务，Solver 会在同一 Agent Session 中持续调用工具。</p>
        </div>
      </header>
      <form className="surface form-surface" onSubmit={submit}>
        <fieldset>
          <legend>基础</legend>
          <label>Session 名称<input value={task.name} onChange={(event) => set("name", event.target.value)} /></label>
          <label>模式
            <select value={task.mode} onChange={(event) => set("mode", event.target.value as TGATask["mode"])}>
              <option value="ctf">CTF 解题</option>
              <option value="web_audit">Web 审计</option>
              <option value="code_audit">代码审计</option>
              <option value="binary_ctf">二进制 CTF</option>
            </select>
          </label>
          <label className="span-2">任务目标<textarea value={task.goal} onChange={(event) => set("goal", event.target.value)} /></label>
        </fieldset>
        <fieldset>
          <legend>目标</legend>
          <label className="span-2">目标地址或路径<input value={task.target} onChange={(event) => set("target", event.target.value)} /></label>
          <label>目标主题<input value={task.target_theme ?? ""} onChange={(event) => set("target_theme", event.target.value)} /></label>
          <label>挑战描述<input value={task.target_description ?? ""} onChange={(event) => set("target_description", event.target.value)} /></label>
        </fieldset>
        <fieldset>
          <legend>初始信息</legend>
          <label>Flag 格式（可选）<input value={task.flag_format ?? ""} onChange={(event) => set("flag_format", event.target.value || null)} /></label>
          <label>初始 Hint（可选）<textarea value={hint} maxLength={800} onChange={(event) => setHint(event.target.value)} placeholder="会直接加入 Solver 的初始上下文。" /></label>
        </fieldset>
        {error ? <div className="inline-error" role="alert">{error}</div> : null}
        <button disabled={busy}>{busy ? "正在创建…" : "创建并进入 Runtime"}</button>
      </form>
    </section>
  );
}
