import type { RuntimeStore } from "../runtime/models/types";

export function RetrievalPanel({ store }: { store: RuntimeStore }) {
  const runs = Object.values(store.retrievalById);
  return <section aria-labelledby="retrieval-title"><header className="runtime-section-title"><div><span>RETRIEVAL</span><h3 id="retrieval-title">检索运行摘要</h3></div><small>{runs.length} 次</small></header>
    {runs.length ? <ul className="runtime-entity-list">{runs.map((run) => <li key={run.retrievalRunId}><div><b>{run.queryPreview || run.retrievalRunId}</b><small>{run.ownerScope} · {run.method} · {run.hitCount} hits</small></div></li>)}</ul> : <p className="runtime-empty">尚无检索运行</p>}
  </section>;
}
