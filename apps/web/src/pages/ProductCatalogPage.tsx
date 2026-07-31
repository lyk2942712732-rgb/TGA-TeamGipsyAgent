import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fetchProductCatalog, type ProductCatalogKind } from "../api/catalog-query-adapter";
import { ErrorState } from "../components/ui/ErrorState";
import { EmptyState } from "../components/ui/EmptyState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { PageHeader } from "../components/ui/PageHeader";

const TITLES: Record<ProductCatalogKind, [string, string]> = {
  resources: ["资源", "Artifacts、Evidence Claims、Findings 的跨任务只读索引。"],
  reports: ["报告", "已导出的任务报告与可追溯来源。"],
  "knowledge-bases": ["知识库", "当前 Schema v6 Retrieval 数据的只读目录。"],
  teams: ["团队模板", "正式 TeamTemplate 与其固定完成策略。"],
  solvers: ["Solver Definitions", "可用于任务编排的 Solver Definition 注册表。"],
  policies: ["策略与预算", "任务创建契约暴露的执行策略目录。"],
  skills: ["Skills", "方法指导不会授予工具执行权限。"],
};

export function ProductCatalogPage({ kind }: { kind: ProductCatalogKind }) {
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const query = params.get("query") ?? "";
  const [title, description] = TITLES[kind];

  const load = async () => {
    setLoading(true); setError("");
    try { const result = await fetchProductCatalog(kind, query); setItems(result.items); if (!result.supported && result.reason) setError(result.reason); }
    catch (reason) { setItems([]); setError(reason instanceof Error ? reason.message : "无法读取目录"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [kind, query]);

  return <section className="page-stack product-catalog-page">
    <PageHeader eyebrow="产品目录" title={title} description={description} />
    <div className="catalog-toolbar"><label>搜索<input aria-label="目录搜索" value={query} onChange={(event) => { const value = event.target.value; setParams((current) => { if (value) current.set("query", value); else current.delete("query"); return current; }, { replace: true }); }} /></label><span>{loading ? "正在读取" : `${items.length} 项`}</span></div>
    {loading ? <LoadingSkeleton label={`${title}加载中`} /> : error ? <ErrorState description={error} actionLabel="重试" onAction={() => void load()} /> : items.length === 0 ? <EmptyState title={`暂无${title}`} description="当前 API 或数据库没有返回可展示的真实记录。" /> : <div className="product-catalog-grid">{items.map((item, index) => <article className="product-catalog-item" key={String(item.id ?? item.name ?? index)}><header><strong>{String(item.name ?? item.title ?? item.id ?? "未命名")}</strong>{item.status ? <span>{String(item.status)}</span> : null}</header><p>{String(item.description ?? item.summary ?? item.kind ?? item.type ?? "当前记录没有附加摘要。")}</p><pre>{JSON.stringify(item, null, 2)}</pre></article>)}</div>}
  </section>;
}
