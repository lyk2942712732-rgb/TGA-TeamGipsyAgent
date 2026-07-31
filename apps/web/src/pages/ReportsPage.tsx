import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Eye, Search } from "lucide-react";
import { apiBase } from "../api/client";
import { BACKEND_CAPABILITIES } from "../api/capability-state";
import { fetchReportsCatalog } from "../api/catalogs";
import { CapabilityNotice, Chip, DisabledAction, ProductEmpty, ProductPageHeader, ProductTable } from "../components/ui/ProductPrimitives";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

export function ReportsPage() {
  const [query, setQuery] = useState("");
  const result = useQuery({ queryKey: ["reports-catalog"], queryFn: fetchReportsCatalog });
  const rows = useMemo(() => (result.data?.items ?? []).filter((item) => !query || `${item.name} ${item.taskId}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())), [query, result.data]);
  return <section className="product-page reports-page">
    <ProductPageHeader title="报告" description="查看、导出和追踪任务报告；编辑与版本流转会在后端提供正式接口后启用。" action={<DisabledAction reason="尚未提供创建报告资源接口">创建报告</DisabledAction>} />
    <div className="product-toolbar">
      <label><span>Task</span><select><option>全部任务</option></select></label><label><span>Mode</span><select><option>全部模式</option></select></label><label><span>状态</span><select><option>全部状态</option><option>draft</option><option>reviewing</option><option>final</option><option>exported</option></select></label>
      <label className="toolbar-search"><Search size={15} /><input aria-label="搜索报告" placeholder="搜索报告名称或 Task ID" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
    </div>
    <CapabilityNotice state={BACKEND_CAPABILITIES.reportCatalog.state} reason={BACKEND_CAPABILITIES.reportCatalog.reason} />
    {result.isLoading ? <LoadingSkeleton label="正在读取报告目录" rows={7} /> : null}
    {result.isError ? <ErrorState title="报告目录加载失败" description={result.error instanceof Error ? result.error.message : "无法读取报告目录"} actionLabel="重试" onAction={() => void result.refetch()} /> : null}
    {!result.isLoading && !result.isError && !rows.length ? <ProductEmpty title="暂无报告" description="报告列表结构已保留。任务生成报告后会出现在这里。" /> : null}
    {rows.length ? <ProductTable label="报告表格" headers={["报告名称", "所属 Task", "Mode", "版本", "状态", "Finding 数", "生成时间", "更新时间", "操作"]}>{rows.map((row) => <tr key={row.id}><td><strong>{row.name}</strong><small>{row.id}</small></td><td><code>{row.taskId}</code></td><td>{row.mode}</td><td>{row.version}</td><td><Chip tone="success">{row.status}</Chip></td><td>{row.findingCount}</td><td>{date(row.generatedAt)}</td><td>{date(row.updatedAt)}</td><td><div className="table-actions"><a href={`${apiBase}/api/v2/tasks/${encodeURIComponent(row.taskId)}/report`} target="_blank" rel="noreferrer" title="查看报告"><Eye size={14} /></a><a href={`${apiBase}/api/v2/tasks/${encodeURIComponent(row.taskId)}/report/export`} title="导出报告"><Download size={14} /></a><button disabled title="尚未提供报告编辑接口">编辑</button><button disabled title="尚未提供版本历史接口">版本</button></div></td></tr>)}</ProductTable> : null}
    <footer className="table-footer"><span>共 {rows.length} 份报告</span><div><button disabled>上一页</button><button className="active">1</button><button disabled>下一页</button></div></footer>
  </section>;
}

const date = (value: string) => value && value !== "-" ? new Date(value).toLocaleString("zh-CN") : "-";

