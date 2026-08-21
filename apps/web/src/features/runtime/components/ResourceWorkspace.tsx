import { runtimeApi } from "../../../runtime/api-v2";
import type { RuntimeStore } from "../models/types";

export function ResourceWorkspace({ store }: { store: RuntimeStore }) {
  const resources = list(record(store.task.raw.task_spec).resources);
  const artifacts = Object.values(store.artifactsById);
  const publications = artifacts.filter((item) => item.kind.includes("publication"));
  const shared = artifacts.filter((item) => !item.kind.includes("publication"));
  return <section className="resource-workspace" aria-labelledby="resources-title"><header className="runtime-section-title"><div><span>RESOURCES</span><h3 id="resources-title">任务资源</h3></div><small>Artifact 正文按需加载</small></header><ResourceSection title="Task Input" values={resources} render={(value, index) => <pre key={index}>{JSON.stringify(value, null, 2)}</pre>} /><ResourceSection title="Shared Artifact" values={shared} render={(item) => <article key={item.artifactId}><b>{item.artifactId}</b><small>{item.kind} · {item.mediaType ?? "unknown"}</small><a href={runtimeApi.artifactUrl(store.task.id, item.artifactId)} target="_blank" rel="noreferrer">按需查看摘要</a></article>} /><ResourceSection title="Solver 发布产物" values={publications} render={(item) => <article key={item.artifactId}><b>{item.artifactId}</b><small>{item.intentId ?? "Task"} · {item.sha256}</small><a href={runtimeApi.artifactUrl(store.task.id, item.artifactId)} target="_blank" rel="noreferrer">按需查看摘要</a></article>} /><p>Solver 私有工作区正文不会在未授权时加载或展示。</p></section>;
}

function ResourceSection<T>({ title, values, render }: { title: string; values: T[]; render: (value: T, index: number) => React.ReactNode }) { return <section><h4>{title}</h4>{values.length ? <div className="resource-card-grid">{values.map(render)}</div> : <small>暂无投影</small>}</section>; }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function list(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
