import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { apiBase, requestJson } from "../api/client";
import { CatalogTable, type Column } from "../components/ui/CatalogTable";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

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
  const columns: Array<Column<ReportRow>> = [
    { id: "title", header: "报告名称", render: (row) => <strong>{row.title}</strong> },
    { id: "task", header: "任务", render: (row) => <button className="text-button" onClick={() => navigate(`/tasks/${encodeURIComponent(row.taskId)}`)}>{row.taskName}</button> },
    { id: "scene", header: "场景", render: (row) => <span className="cell-muted">{row.scene}</span> },
    { id: "status", header: "状态", render: () => <span className="ref-chip tone-ok">已生成</span> },
    { id: "updated", header: "生成时间", render: (row) => <span className="cell-muted">{new Date(row.updatedAt).toLocaleString("zh-CN")}</span> },
    { id: "actions", header: "操作", render: (row) => <a className="ref-secondary-button" href={`${apiBase}/api/v3/tasks/${encodeURIComponent(row.taskId)}/writeup/download`}><Download size={14} />下载 Markdown</a> },
  ];
  return <div className="ref-page"><header className="ref-page-head"><div><span className="eyebrow">WRITEUPS</span><h1>报告</h1><p>Reporter 根据最终黑板快照生成的 Markdown writeup。</p></div></header>{query.isLoading ? <LoadingSkeleton label="正在读取报告" rows={6} /> : query.isError ? <ErrorState description={query.error instanceof Error ? query.error.message : "无法读取报告"} actionLabel="重试" onAction={() => void query.refetch()} /> : query.data?.length ? <section className="ref-card ref-fill"><CatalogTable columns={columns} rows={query.data} rowKey={(row) => row.id} /></section> : <EmptyState title="还没有报告" description="只有出现最终候选并由 Reporter 完成总结后，这里才会出现真实 writeup。" />}</div>;
}
