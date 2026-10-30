import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, Search } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiBase, requestJson } from "../api/client";
import { CatalogTable, type Column } from "../components/ui/CatalogTable";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { TGA3ReportReader } from "../features/runtime/components/TGA3ReportReader";
import { MODE_PROFILES } from "../modes";

type ReportRow = { id: string; taskId: string; title: string; taskName: string; scene: string; updatedAt: string };

async function fetchReports(): Promise<ReportRow[]> {
  const tasks = await requestJson<Array<{ id: string; title: string; scene_id: string; updated_at: string }>>("/api/v3/tasks");
  const rows = await Promise.all(tasks.map(async (task) => {
    try {
      const writeup = await requestJson<{ id: string; created_at: string }>(`/api/v3/tasks/${encodeURIComponent(task.id)}/writeup`);
      return { id: writeup.id, taskId: task.id, title: `${task.title} Writeup`, taskName: task.title, scene: task.scene_id, updatedAt: writeup.created_at };
    } catch { return null; }
  }));
  return rows.filter((row): row is ReportRow => row !== null);
}

export function ReportsPage() {
  const navigate = useNavigate();
  const query = useQuery({ queryKey: ["tga3", "reports"], queryFn: fetchReports });
  const [selected, setSelected] = useState<ReportRow | null>(null);
  const [search, setSearch] = useState("");
  const columns: Array<Column<ReportRow>> = [
    { id: "title", header: "报告名称", render: (row) => <button className="text-button" onClick={() => setSelected(row)}><strong>{row.title}</strong></button> },
    { id: "task", header: "任务", render: (row) => <button className="text-button" onClick={(event) => { event.stopPropagation(); navigate(`/tasks/${encodeURIComponent(row.taskId)}`); }}>{row.taskName}</button> },
    { id: "scene", header: "场景", render: (row) => <span className="cell-muted">{MODE_PROFILES[row.scene as keyof typeof MODE_PROFILES]?.label ?? row.scene}</span> },
    { id: "status", header: "状态", render: () => <span className="ref-chip tone-ok">已生成</span> },
    { id: "updated", header: "生成时间", render: (row) => <span className="cell-muted">{new Date(row.updatedAt).toLocaleString("zh-CN")}</span> },
    { id: "actions", header: "操作", render: (row) => <a className="ref-secondary-button" onClick={event => event.stopPropagation()} href={`${apiBase}/api/v3/tasks/${encodeURIComponent(row.taskId)}/writeup/download`}><Download size={14} />下载 Markdown</a> },
  ];
  if (selected) return <div className="ref-page"><header className="ref-page-head"><div><h1>{selected.taskName}</h1><p>解题报告 / Reporter</p></div><button className="ref-secondary-button" onClick={() => setSelected(null)}><ArrowLeft size={14} />返回报告列表</button></header><section className="ref-card"><TGA3ReportReader taskId={selected.taskId} /></section></div>;
  const visible = (query.data ?? []).filter(row => `${row.title} ${row.taskName}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="ref-page"><header className="ref-page-head"><div><h1>报告</h1><p>已完成任务的解题过程、结果与证据。</p></div></header>{query.data?.length ? <label className="ref-search is-wide"><Search size={15} /><input aria-label="搜索报告" placeholder="搜索报告或任务" value={search} onChange={event => setSearch(event.target.value)} /></label> : null}{query.isLoading ? <LoadingSkeleton label="正在读取报告" rows={6} /> : query.isError ? <ErrorState description={query.error instanceof Error ? query.error.message : "无法读取报告"} actionLabel="重试" onAction={() => void query.refetch()} /> : query.data?.length ? <CatalogTable columns={columns} rows={visible} rowKey={(row) => row.id} onSelect={setSelected} /> : <EmptyState title="还没有报告" description="Reporter 完成解题总结后，这里会出现对应的 Writeup。" />}</div>;
}
