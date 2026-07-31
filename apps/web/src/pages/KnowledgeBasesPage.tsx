import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Database, FileText, Search } from "lucide-react";
import { BACKEND_CAPABILITIES } from "../api/capability-state";
import { fetchKnowledgeBasesCatalog } from "../api/catalogs";
import { CapabilityNotice, Chip, DisabledAction, ProductEmpty, ProductPageHeader, ProductTable, ProductTabs } from "../components/ui/ProductPrimitives";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

const TABS = ["知识库", "Sources", "Documents", "Index Snapshots", "检索测试"];

export function KnowledgeBasesPage() {
  const [tab, setTab] = useState("知识库"); const [query, setQuery] = useState("");
  const result = useQuery({ queryKey: ["knowledge-bases-catalog"], queryFn: fetchKnowledgeBasesCatalog });
  const rows = useMemo(() => (result.data?.items ?? []).filter((item) => !query || `${item.name} ${item.scope}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())), [query, result.data]);
  return <section className="product-page knowledge-page">
    <ProductPageHeader title="知识库" description="管理跨任务知识源、文档和索引快照，并检查检索可用性。" action={<DisabledAction reason="尚未提供 Knowledge Base 创建接口">新建知识库</DisabledAction>} />
    <ProductTabs items={TABS} active={tab} onChange={setTab} />
    <div className="product-toolbar"><label className="toolbar-search"><Search size={15} /><input aria-label="搜索知识库" placeholder="搜索名称或 Scope" value={query} onChange={(event) => setQuery(event.target.value)} /></label><label><span>类型</span><select><option>全部类型</option></select></label><label><span>Scope</span><select><option>全部 Scope</option></select></label><label><span>状态</span><select><option>全部状态</option></select></label></div>
    <CapabilityNotice state={BACKEND_CAPABILITIES.knowledgeCatalog.state} reason={tab === "知识库" ? BACKEND_CAPABILITIES.knowledgeCatalog.reason : `${tab} 的管理接口尚未提供`} />
    {tab === "知识库" ? <>
      {result.isLoading ? <LoadingSkeleton label="正在读取知识库目录" rows={6} /> : null}
      {result.isError ? <ErrorState title="知识库目录加载失败" description={result.error instanceof Error ? result.error.message : "无法读取知识库目录"} actionLabel="重试" onAction={() => void result.refetch()} /> : null}
      {!result.isLoading && !result.isError && !rows.length ? <ProductEmpty title="暂无知识库" description="Catalog 没有返回真实知识库记录；页面结构和只读状态已保留。" /> : null}
      {rows.length ? <ProductTable label="知识库表格" headers={["名称", "类型", "Scope", "文档数", "Source 数", "索引版本", "状态", "最后同步时间", "操作"]}>{rows.map((row) => <tr key={row.id}><td><div className="entity-name"><Database size={17} /><span><strong>{row.name}</strong><small>{row.id}</small></span></div></td><td>{row.type}</td><td>{row.scope}</td><td>{row.documentCount}</td><td>{row.sourceCount}</td><td><code>{row.indexVersion}</code></td><td><Chip tone="success">{row.status}</Chip></td><td>{date(row.lastSyncAt)}</td><td><div className="table-actions"><button className="link-button">查看</button><button disabled title="尚未提供手动同步接口">同步</button></div></td></tr>)}</ProductTable> : null}
    </> : <UnsupportedKnowledgeTab tab={tab} />}
    <div className="knowledge-summary-grid"><article><Database size={18} /><div><strong>索引状态摘要</strong><p>{rows.length} 个知识库目录记录，索引健康度仅展示后端可验证字段。</p></div></article><article><FileText size={18} /><div><strong>最近同步</strong><p>{rows[0]?.lastSyncAt && rows[0].lastSyncAt !== "-" ? date(rows[0].lastSyncAt) : "尚无可验证的同步记录"}</p></div></article></div>
  </section>;
}

function UnsupportedKnowledgeTab({ tab }: { tab: string }) { return <section className="unsupported-workspace"><h2>{tab}</h2><div className="product-toolbar"><label className="toolbar-search"><Search size={15} /><input disabled placeholder={`搜索 ${tab}`} /></label><DisabledAction reason={`尚未提供 ${tab} 管理接口`}>新增</DisabledAction></div><ProductEmpty title={`暂无 ${tab} 数据`} description={`当前后端尚未提供 ${tab} HTTP API，未伪造生产记录。`} /></section>; }
const date = (value: string) => value && value !== "-" ? new Date(value).toLocaleString("zh-CN") : "-";

