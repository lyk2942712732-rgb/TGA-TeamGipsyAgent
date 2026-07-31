import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, ShieldCheck } from "lucide-react";
import { BACKEND_CAPABILITIES } from "../api/capability-state";
import { fetchPoliciesCatalog } from "../api/catalogs";
import { CapabilityNotice, Chip, DefinitionList, DisabledAction, ProductEmpty, ProductPageHeader, ProductTabs } from "../components/ui/ProductPrimitives";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

const TABS = ["执行策略", "工具策略", "预算模板", "保留策略"];

export function PoliciesPage() {
  const [tab, setTab] = useState("执行策略"); const [selectedId, setSelectedId] = useState("");
  const result = useQuery({ queryKey: ["policies-catalog"], queryFn: fetchPoliciesCatalog });
  const rows = result.data?.items ?? []; const selected = rows.find((item) => item.id === selectedId) ?? rows[0];
  return <section className="product-page policies-page">
    <ProductPageHeader title="策略与预算" description="查看执行边界、工具治理、预算模板和保留策略。" />
    <ProductTabs items={TABS} active={tab} onChange={setTab} />
    <CapabilityNotice state={BACKEND_CAPABILITIES.policyCatalog.state} reason={tab === "执行策略" ? BACKEND_CAPABILITIES.policyCatalog.reason : `${tab} 目录和版本管理接口尚未提供`} />
    {tab === "执行策略" ? <>
      {result.isLoading ? <LoadingSkeleton label="正在读取执行策略" rows={8} /> : null}
      {result.isError ? <ErrorState title="策略目录加载失败" description={result.error instanceof Error ? result.error.message : "无法读取策略目录"} actionLabel="重试" onAction={() => void result.refetch()} /> : null}
      {!result.isLoading && !result.isError && !selected ? <ProductEmpty title="暂无执行策略" description="策略目录未返回真实记录。" /> : null}
      {selected ? <div className="policy-layout"><aside className="policy-list"><label className="toolbar-search"><Search size={15} /><input placeholder="搜索策略" /></label>{rows.map((item) => <button key={item.id} className={item.id === selected.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span className="master-icon"><ShieldCheck size={17} /></span><div><strong>{item.name}</strong><small>{item.mode}</small><span>网络：{item.networkAccess} · 高影响：{item.highImpact}</span></div><Chip tone="success">{item.status}</Chip></button>)}</aside><article className="detail-panel"><header className="detail-panel-header"><div><span className="detail-kicker">EXECUTION POLICY</span><h2>{selected.name}</h2><p>{selected.description}</p></div><Chip tone="info">{selected.status}</Chip></header><div className="detail-columns"><section><h3>网络边界</h3><DefinitionList rows={[["网络访问", selected.networkAccess], ["私网限制", selected.denyPrivate], ["回环限制", selected.denyLoopback], ["Link-local 限制", selected.denyLinkLocal], ["Cloud Metadata 限制", selected.denyMetadata], ["Rate Limit", selected.rateLimit], ["Concurrency", selected.concurrency], ["Timeout", selected.timeout]]} /></section><section><h3>执行与预算</h3><DefinitionList rows={[["Local Compute", selected.localCompute], ["高影响操作", selected.highImpact], ["最大运行时间", selected.maxRuntime], ["Token 上限", selected.tokenLimit], ["Tool Call 上限", selected.toolCallLimit], ["Artifact 上限", selected.artifactLimit]]} /></section></div><footer className="detail-footer"><DisabledAction reason="尚未提供复制策略接口">复制模板</DisabledAction><DisabledAction reason="尚未提供策略版本管理接口">编辑新版本</DisabledAction></footer></article></div> : null}
    </> : <section className="unsupported-workspace"><h2>{tab}</h2><div className="product-toolbar"><label className="toolbar-search"><Search size={15} /><input disabled placeholder={`搜索${tab}`} /></label><DisabledAction reason={`尚未提供${tab}管理接口`}>新建</DisabledAction></div><ProductEmpty title={`暂无${tab}目录`} description="页面结构已保留，当前后端未提供该目录的真实生产数据。" /></section>}
  </section>;
}
