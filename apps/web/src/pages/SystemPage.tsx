import { useEffect, useState } from "react";
import { fetchSystemHealth, type SystemComponent } from "../api/catalog-query-adapter";
import { ErrorState } from "../components/ui/ErrorState";
import { EmptyState } from "../components/ui/EmptyState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { PageHeader } from "../components/ui/PageHeader";

export function SystemPage() {
  const [items, setItems] = useState<SystemComponent[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  const load = async () => { setLoading(true); setError(""); try { setItems((await fetchSystemHealth()).components); } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取系统状态"); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  return <section className="page-stack system-page"><PageHeader eyebrow="诊断" title="系统状态" description="Provider、MCP、Runtime 与 Retrieval 的真实健康检查。" />{loading ? <LoadingSkeleton label="系统状态加载中" /> : error ? <ErrorState description={error} actionLabel="重试" onAction={() => void load()} /> : !items.length ? <EmptyState title="暂无诊断数据" /> : <div className="system-status-grid">{items.map((item) => <article key={item.id}><header><strong>{item.label}</strong><span className={`system-status-${item.status}`}>{item.status}</span></header><p>{item.detail}</p>{item.lastError ? <small>{item.lastError}</small> : null}</article>)}</div>}</section>;
}
