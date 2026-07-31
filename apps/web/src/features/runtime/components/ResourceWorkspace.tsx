import { runtimeApi } from "../../../runtime/api-v2";
import type { RuntimeStore } from "../models/types";

export function ResourceWorkspace({ store }: { store: RuntimeStore }) {
  const taskInput = record(store.task.raw.session_input);
  const files = list(taskInput.files);
  const resources = list(record(store.task.raw.task_spec).resources);
  const artifacts = Object.values(store.artifactsById);
  const publications = artifacts.filter((item) => item.kind.includes("publication"));
  const shared = artifacts.filter((item) => !item.kind.includes("publication"));
  return <section className="resource-workspace" aria-labelledby="resources-title"><header className="runtime-section-title"><div><span>RESOURCES</span><h3 id="resources-title">任务资源</h3></div><small>Artifact 正文按需加载</small></header><ResourceSection title="Task Input" values={[...files, ...resources]} render={(value, index) => <InputResource key={index} value={value} index={index} />} /><ResourceSection title="Shared Artifact" values={shared} render={(item) => <article key={item.artifactId}><b>{item.artifactId}</b><small>{item.kind} · {item.mediaType ?? "unknown"}</small><a href={runtimeApi.artifactUrl(store.task.id, item.artifactId)} target="_blank" rel="noreferrer">按需查看摘要</a></article>} /><ResourceSection title="Solver 发布产物" values={publications} render={(item) => <article key={item.artifactId}><b>{item.artifactId}</b><small>{item.intentId ?? "Task"} · {item.sha256}</small><a href={runtimeApi.artifactUrl(store.task.id, item.artifactId)} target="_blank" rel="noreferrer">按需查看摘要</a></article>} /><ResourceSection title="RAG Source / Index 摘要" values={Object.values(store.retrievalById)} render={(run) => <article key={run.retrievalRunId}><b>{run.queryPreview || run.retrievalRunId}</b><small>{run.indexSnapshotId} · {run.hitCount} hits · {run.ownerScope}</small></article>} /><p>Solver 私有工作区正文不会在未授权时加载或展示。</p></section>;
}

function ResourceSection<T>({ title, values, render }: { title: string; values: T[]; render: (value: T, index: number) => React.ReactNode }) { return <section><h4>{title}</h4>{values.length ? <div className="resource-card-grid">{values.map(render)}</div> : <small>暂无投影</small>}</section>; }
function InputResource({ value, index }: { value: unknown; index: number }) { const item = record(value); const name = String(item.label ?? item.original_name ?? item.name ?? item.url ?? `Input ${index + 1}`); const type = String(item.mime_type ?? item.type ?? item.kind ?? "resource"); const size = item.size != null ? `${String(item.size)} bytes` : ""; return <article><b>{name}</b><small>{[type, size].filter(Boolean).join(" · ")}</small></article>; }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function list(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
