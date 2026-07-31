import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, Search } from "lucide-react";
import { BACKEND_CAPABILITIES } from "../api/capability-state";
import { fetchSolverDefinitionsCatalog } from "../api/catalogs";
import { CapabilityNotice, Chip, DefinitionList, DisabledAction, ProductEmpty, ProductPageHeader, ProductTabs } from "../components/ui/ProductPrimitives";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

const TABS = ["基础配置", "Instructions 模板", "能力 Tools", "默认 Skills", "输出合约", "版本"];

export function SolversPage() {
  const [query, setQuery] = useState(""); const [selectedId, setSelectedId] = useState(""); const [tab, setTab] = useState("基础配置");
  const result = useQuery({ queryKey: ["solver-definitions-catalog"], queryFn: fetchSolverDefinitionsCatalog });
  const rows = useMemo(() => (result.data?.items ?? []).filter((item) => !query || `${item.name} ${item.role} ${item.specialties.join(" ")}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())), [query, result.data]);
  const selected = rows.find((item) => item.id === selectedId) ?? rows[0];
  return <section className="product-page master-detail-page solvers-page">
    <ProductPageHeader title="Solver Definitions" description="查看 Solver 的角色、能力、默认 Skills、输出合约和冻结版本。" action={<DisabledAction reason="Solver Definition 注册表当前仅支持只读">新建 Solver</DisabledAction>} />
    <div className="product-toolbar"><label className="toolbar-search"><Search size={15} /><input aria-label="搜索 Solver" placeholder="搜索名称、ID 或专长" value={query} onChange={(event) => setQuery(event.target.value)} /></label><label><span>Role</span><select><option>全部角色</option></select></label><label><span>Status</span><select><option>全部状态</option></select></label><label><span>Mode</span><select><option>全部模式</option></select></label></div>
    <CapabilityNotice state={BACKEND_CAPABILITIES.solverDefinitions.state} reason={BACKEND_CAPABILITIES.solverDefinitions.reason} />
    {result.isLoading ? <LoadingSkeleton label="正在读取 Solver Definitions" rows={8} /> : null}
    {result.isError ? <ErrorState title="Solver Definitions 加载失败" description={result.error instanceof Error ? result.error.message : "无法读取 Solver Definitions"} actionLabel="重试" onAction={() => void result.refetch()} /> : null}
    {!result.isLoading && !result.isError && !rows.length ? <ProductEmpty title="暂无 Solver Definition" description="内置注册表未返回可展示定义。" /> : null}
    {selected ? <div className="master-detail-layout"><aside className="master-list solver-master-list" aria-label="Solver 列表">{rows.map((item) => <button key={item.id} className={item.id === selected.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span className="master-icon"><Bot size={17} /></span><div><strong>{item.name}</strong><small>{item.role} · {item.version}</small><span>{item.specialties.slice(0, 3).join(" · ") || "未声明专长"}</span></div><Chip tone="success">{item.status}</Chip></button>)}</aside><article className="detail-panel"><header className="detail-panel-header"><div><span className="detail-kicker">SOLVER DEFINITION</span><h2>{selected.name}</h2><p>{selected.id} · {selected.version}</p></div><div><Chip tone="info">只读</Chip><DisabledAction reason="当前后端仅支持只读">编辑</DisabledAction></div></header><DefinitionList rows={[["Role", selected.role], ["支持 Mode", selected.modes.join("、")], ["Specialties", selected.specialties.join("、")], ["Completion Authority", selected.completionAuthority], ["Tool Policy Profile", selected.toolPolicy], ["默认预算", selected.budgetSummary]]} /><ProductTabs items={TABS} active={tab} onChange={setTab} label="Solver 详情" /><SolverTab tab={tab} solver={selected} /></article></div> : null}
  </section>;
}

function SolverTab({ tab, solver }: { tab: string; solver: Awaited<ReturnType<typeof fetchSolverDefinitionsCatalog>>["items"][number] }) {
  if (tab === "Instructions 模板") return <section className="text-detail"><h3>System Prompt Template</h3><p>{solver.instructions}</p></section>;
  if (tab === "能力 Tools") return <section className="tag-detail"><h3>Required Capabilities</h3><div>{solver.capabilities.map((item) => <Chip key={item} tone="info">{item}</Chip>)}</div><h3>Allowed Tool Groups</h3><div>{solver.toolGroups.map((item) => <Chip key={item}>{item}</Chip>)}</div></section>;
  if (tab === "默认 Skills") return <section className="tag-detail"><h3>Default Skill Tags</h3><div>{solver.skillTags.map((item) => <Chip key={item}>{item}</Chip>)}</div><h3>Required Skills</h3><div>{solver.requiredSkills.map((item) => <Chip key={item} tone="info">{item}</Chip>)}</div></section>;
  if (tab === "输出合约") return <section className="text-detail"><h3>Output Contract</h3><p>{solver.outputContract}</p><h3>Accepted Intent Kinds</h3><p>{solver.intentKinds.join("、") || "未声明"}</p></section>;
  if (tab === "版本") return <section className="text-detail"><h3>Content SHA256</h3><code>{solver.contentHash}</code><CapabilityNotice state="unsupported" reason="尚未提供 Solver Definition 版本管理接口" /></section>;
  return <section className="detail-columns"><div><h3>定义身份</h3><DefinitionList rows={[["Definition ID", solver.id], ["Version", solver.version], ["Role", solver.role], ["状态", solver.status]]} /></div><div><h3>执行边界</h3><DefinitionList rows={[["Completion Authority", solver.completionAuthority], ["Tool Policy", solver.toolPolicy], ["Default Budget", solver.budgetSummary]]} /></div></section>;
}

