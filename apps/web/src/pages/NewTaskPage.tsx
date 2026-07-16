import { FormEvent, useState } from "react";
import { createTask, type TGATask } from "../api/tasks";

/**
 * `crypto.randomUUID` is intentionally unavailable on an HTTP page served
 * from a public IP.  Task ids only need a short local uniqueness suffix, so
 * fall back safely instead of making the entire creation page fail to render.
 */
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

const defaultTask = (): TGATask => ({ id: newTaskId(), name: "新建安全任务", mode: "ctf", target: "http://127.0.0.1:8080", scope: ["127.0.0.1:8080"], target_theme: "", target_description: "", intensity: "normal", allow_active_scan: false, goal: "在授权范围内收集证据、验证假设并产出可追溯结论。", flag_format: "(?:flag|NSSCTF)\\{[^}]+\\}" });

export function NewTaskPage({ onCreated }: { onCreated: (id: string) => void }) {
  const [task, setTask] = useState(defaultTask); const [hint, setHint] = useState(""); const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const set = <K extends keyof TGATask>(key: K, value: TGATask[K]) => setTask((current) => ({ ...current, [key]: value }));
  const submit = async (event: FormEvent) => { event.preventDefault(); if (!task.name.trim() || !task.target.trim() || !task.goal.trim()) return setError("请填写名称、目标和任务目标。"); if (task.mode === "web_audit" && !task.scope.length) return setError("Web 审计必须提供授权范围。"); setBusy(true); setError(null); try { const result = await createTask(task, hint.trim() || undefined); onCreated(result.task_id); } catch (reason) { setError(reason instanceof Error ? reason.message : "创建 Session 失败"); } finally { setBusy(false); } };
  return <section className="page-stack"><header className="page-title"><div><span className="eyebrow">Sessions / New</span><h1>新建 Session</h1><p>即时校验帮助减少配置错误；授权和执行策略仍由后端裁决。</p></div></header><form className="surface form-surface" onSubmit={submit}><fieldset><legend>基础</legend><label>Session 名称<input value={task.name} onChange={(e) => set("name", e.target.value)} /></label><label>模式<select value={task.mode} onChange={(e) => set("mode", e.target.value as TGATask["mode"])}><option value="ctf">CTF 解题</option><option value="web_audit">Web 审计</option><option value="code_audit">代码审计</option><option value="binary_ctf">二进制 CTF</option></select></label><label className="span-2">任务目标<textarea value={task.goal} onChange={(e) => set("goal", e.target.value)} /></label></fieldset><fieldset><legend>目标与范围</legend><label>目标地址<input type="url" value={task.target} onChange={(e) => set("target", e.target.value)} /></label><label>授权范围（逗号分隔）<input value={task.scope.join(", ")} onChange={(e) => set("scope", e.target.value.split(",").map((item) => item.trim()).filter(Boolean))} /></label><label>目标主题<input value={task.target_theme ?? ""} onChange={(e) => set("target_theme", e.target.value)} /></label><label>挑战描述<input value={task.target_description ?? ""} onChange={(e) => set("target_description", e.target.value)} /></label></fieldset><fieldset><legend>执行策略与初始信息</legend><label>执行强度<select value={task.intensity} onChange={(e) => set("intensity", e.target.value as TGATask["intensity"])}><option value="passive">被动</option><option value="normal">标准</option><option value="active">主动</option></select></label><label>Flag 格式<input value={task.flag_format ?? ""} onChange={(e) => set("flag_format", e.target.value || null)} /></label><label className="check-row"><input type="checkbox" checked={task.allow_active_scan} onChange={(e) => set("allow_active_scan", e.target.checked)} />允许在授权范围内主动探测</label><label>初始 hint（可选）<textarea value={hint} maxLength={800} onChange={(e) => setHint(e.target.value)} placeholder="会进入策略记忆，不能绕过执行策略。" /></label></fieldset>{error ? <div className="inline-error" role="alert">{error}</div> : null}<button disabled={busy}>{busy ? "正在创建…" : "创建并进入 Runtime"}</button></form></section>;
}
