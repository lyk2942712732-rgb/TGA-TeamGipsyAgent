import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, Users } from "lucide-react";
import { BACKEND_CAPABILITIES } from "../api/capability-state";
import { fetchTeamTemplatesCatalog } from "../api/catalogs";
import { CapabilityNotice, Chip, DefinitionList, DisabledAction, ProductEmpty, ProductPageHeader } from "../components/ui/ProductPrimitives";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

export function TeamTemplatesPage() {
  const [query, setQuery] = useState(""); const [selectedId, setSelectedId] = useState("");
  const result = useQuery({ queryKey: ["team-templates-catalog"], queryFn: fetchTeamTemplatesCatalog });
  const rows = useMemo(() => (result.data?.items ?? []).filter((item) => !query || `${item.name} ${item.mode}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())), [query, result.data]);
  const selected = rows.find((item) => item.id === selectedId) ?? rows[0];
  return <section className="product-page master-detail-page team-templates-page">
    <ProductPageHeader title="团队模板" description="浏览内置团队编排结构、角色分配和完成策略。" action={<DisabledAction reason="当前 Team Template 仅支持只读">新建模板</DisabledAction>} />
    <div className="product-toolbar"><label className="toolbar-search"><Search size={15} /><input aria-label="搜索团队模板" placeholder="搜索模板名称" value={query} onChange={(event) => setQuery(event.target.value)} /></label><label><span>Mode</span><select><option>全部模式</option></select></label><label><span>状态</span><select><option>全部状态</option></select></label><div className="toolbar-spacer" /><button className="view-toggle active">列表</button><button className="view-toggle">卡片</button></div>
    <CapabilityNotice state={BACKEND_CAPABILITIES.teamTemplates.state} reason={BACKEND_CAPABILITIES.teamTemplates.reason} />
    {result.isLoading ? <LoadingSkeleton label="正在读取团队模板" rows={8} /> : null}
    {result.isError ? <ErrorState title="团队模板加载失败" description={result.error instanceof Error ? result.error.message : "无法读取团队模板"} actionLabel="重试" onAction={() => void result.refetch()} /> : null}
    {!result.isLoading && !result.isError && !rows.length ? <ProductEmpty title="暂无团队模板" description="内置注册表未返回可展示模板。" /> : null}
    {selected ? <div className="master-detail-layout"><aside className="master-list" aria-label="团队模板列表">{rows.map((item) => <button key={item.id} className={item.id === selected.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span className="master-icon"><Users size={17} /></span><div><strong>{item.name}</strong><small>{item.mode}</small><span>{item.supervisor}</span></div><Chip tone="success">{item.status}</Chip></button>)}</aside><article className="detail-panel"><header className="detail-panel-header"><div><span className="detail-kicker">TEAM TEMPLATE</span><h2>{selected.name}</h2><p>{selected.id}</p></div><div><Chip tone="info">{selected.mode}</Chip><DisabledAction reason="当前后端仅支持只读">编辑模板</DisabledAction></div></header><section className="team-topology"><Role title="Supervisor" values={[selected.supervisor]} /><span>→</span><Role title="Required Workers" values={selected.requiredWorkers} /><span>→</span><Role title="Reviewer" values={[selected.reviewer]} /><span>→</span><Role title="Reporter" values={[selected.reporter]} /></section><div className="detail-columns"><section><h3>团队配置</h3><DefinitionList rows={[["Available Workers", selected.availableWorkers.join("、")], ["最大并行 Solver", selected.maxParallel], ["最大 Solver 总数", selected.maxSolvers], ["Spawn Rules", selected.spawnRules.join("；")], ["Completion Policy", selected.completionPolicy]]} /></section><section><h3>策略与版本</h3><DefinitionList rows={[["默认策略摘要", selected.policySummary], ["Hash / 版本", selected.version], ["更新时间", selected.updatedAt], ["能力状态", "read_only"]]} /></section></div><footer className="detail-footer"><DisabledAction reason="尚未提供复制模板接口">复制模板</DisabledAction><DisabledAction reason="尚未提供版本管理接口">版本历史</DisabledAction></footer></article></div> : null}
  </section>;
}

function Role({ title, values }: { title: string; values: string[] }) { return <article><small>{title}</small><strong>{values.filter((value) => value && value !== "-").join("、") || "未配置"}</strong></article>; }

