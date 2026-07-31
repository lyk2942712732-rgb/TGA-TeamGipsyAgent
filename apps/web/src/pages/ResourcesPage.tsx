import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Eye, Search } from "lucide-react";
import { fetchResourceCatalog } from "../api/catalogs";
import { CapabilityNotice, Chip, ProductEmpty, ProductPageHeader, ProductTable, ProductTabs } from "../components/ui/ProductPrimitives";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

const TABS = ["Artifacts", "Evidence Claims", "Findings", "Knowledge"];

export function ResourcesPage() {
  const [tab, setTab] = useState("Artifacts");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const result = useQuery({ queryKey: ["resource-catalog"], queryFn: fetchResourceCatalog });
  const needle = query.trim().toLocaleLowerCase();
  const visible = useMemo(() => {
    const source = tab === "Artifacts" ? result.data?.artifacts : tab === "Evidence Claims" ? result.data?.evidence : tab === "Findings" ? result.data?.findings : result.data?.knowledge;
    return (source ?? []).filter((item) => (!status || item.status === status) && (!needle || Object.values(item).some((value) => String(value).toLocaleLowerCase().includes(needle))));
  }, [needle, result.data, status, tab]);

  return <section className="product-page resources-page">
    <ProductPageHeader title="资源" description="跨任务查看 Artifact、Evidence Claim、Finding 与 Knowledge，并沿证据链逐层下钻。" />
    <ProductTabs items={TABS} active={tab} onChange={setTab} />
    <div className="product-toolbar">
      <label><span>Task</span><select aria-label="Task 筛选"><option>全部任务</option></select></label>
      <label><span>类型</span><select aria-label="资源类型"><option>全部类型</option></select></label>
      <label><span>状态</span><select aria-label="资源状态" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option><option value="available">Available</option><option value="confirmed">Confirmed</option><option value="candidate">Candidate</option></select></label>
      <label className="toolbar-search"><Search size={15} /><input aria-label="搜索资源" placeholder="搜索名称、目标或 Hash" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <label><span>时间范围</span><select aria-label="时间范围"><option>最近 30 天</option><option>全部时间</option></select></label>
    </div>
    {result.data ? <CapabilityNotice state={result.data.capability} reason={result.data.reason || "当前目录仅支持只读"} /> : null}
    {result.isLoading ? <LoadingSkeleton label="正在读取资源目录" rows={7} /> : null}
    {result.isError ? <ErrorState title="资源目录加载失败" description={result.error instanceof Error ? result.error.message : "无法读取资源目录"} actionLabel="重试" onAction={() => void result.refetch()} /> : null}
    {!result.isLoading && !result.isError && !visible.length ? <ProductEmpty title={`暂无 ${tab}`} description="页面结构已保留。当前真实目录中没有符合筛选条件的记录。" /> : null}
    {visible.length ? <ResourceTable tab={tab} rows={visible} /> : null}
    <footer className="table-footer"><span>共 {visible.length} 条记录</span><div><button disabled>上一页</button><button className="active">1</button><button disabled>下一页</button></div></footer>
  </section>;
}

function ResourceTable({ tab, rows }: { tab: string; rows: any[] }) {
  if (tab === "Artifacts") return <ProductTable label="Artifacts 表格" headers={["名称或目标", "类型", "Task", "来源 Solver", "来源 Intent", "大小 / 媒体类型", "Hash", "创建时间", "状态", "操作"]}>{rows.map((row) => <tr key={row.id}><td><strong>{row.name}</strong><small>{row.id}</small></td><td>{row.type}</td><td><code>{row.taskId}</code></td><td>{row.sourceSolver}</td><td>{row.sourceIntent}</td><td>{row.media}</td><td><code>{row.hash}</code></td><td>{date(row.createdAt)}</td><td><Chip tone="success">{row.status}</Chip></td><td><div className="table-actions"><button title="预览 Artifact"><Eye size={14} /></button><button title="下载 Artifact"><Download size={14} /></button></div></td></tr>)}</ProductTable>;
  if (tab === "Evidence Claims") return <ProductTable label="Evidence Claims 表格" headers={["Statement", "Artifact", "Locator", "状态", "创建者", "Reviewer", "时间", "操作"]}>{rows.map((row) => <tr key={row.id}><td><strong>{row.statement}</strong><small>{row.id}</small></td><td><code>{row.artifact}</code></td><td>{row.locator}</td><td><Chip tone="info">{row.status}</Chip></td><td>{row.creator}</td><td>{row.reviewer}</td><td>{date(row.createdAt)}</td><td><button className="link-button">下钻</button></td></tr>)}</ProductTable>;
  if (tab === "Findings") return <ProductTable label="Findings 表格" headers={["标题", "Severity", "Target", "状态", "Evidence 数量", "创建者", "时间", "操作"]}>{rows.map((row) => <tr key={row.id}><td><strong>{row.title}</strong><small>{row.id}</small></td><td><Chip tone={row.severity === "critical" || row.severity === "high" ? "danger" : "warning"}>{row.severity}</Chip></td><td>{row.target}</td><td><Chip tone="info">{row.status}</Chip></td><td>{row.evidenceCount}</td><td>{row.creator}</td><td>{date(row.createdAt)}</td><td><button className="link-button">查看证据链</button></td></tr>)}</ProductTable>;
  return <ProductTable label="Knowledge 表格" headers={["类型", "Scope", "Target", "状态", "来源 Solver", "时间", "操作"]}>{rows.map((row) => <tr key={row.id}><td>{row.type}</td><td>{row.scope}</td><td><strong>{row.target}</strong></td><td><Chip tone="info">{row.status}</Chip></td><td>{row.sourceSolver}</td><td>{date(row.createdAt)}</td><td><button className="link-button">查看</button></td></tr>)}</ProductTable>;
}

const date = (value: string) => value && value !== "-" ? new Date(value).toLocaleString("zh-CN") : "-";

