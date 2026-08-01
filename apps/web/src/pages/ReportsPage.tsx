import { useQuery } from "@tanstack/react-query";
import { Download, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchProductCatalog } from "../api/catalog-query-adapter";
import { CatalogTable, Pagination, usePage, type Column } from "../components/ui/CatalogTable";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { useToast } from "../components/ui/Toast";
import { runtimeApi } from "../runtime/api-v2";
import { MODE_PROFILES } from "../modes";

const reportUrl = (taskId: string) => runtimeApi.reportUrl(taskId);

/**
 * 报告中心 (reference image 08).
 *
 * `/api/v2/catalog/reports` lists exported `report.md` files and supplies only
 * id / task_id / status / title / updated_at.  模式, 版本 and Findings have no
 * source at all, so those columns show a dash.
 */

type ReportRow = {
  id: string;
  taskId: string;
  title: string;
  taskName: string;
  mode: string | null;
  version: string | null;
  status: string;
  findings: number | null;
  updatedAt: string;
};

type CatalogReport = { id: string; task_id: string; status: string; title: string; updated_at: number };

const STATUS_TONES: Record<string, string> = {
  final: "tone-ok", completed: "tone-ok",
  reviewing: "tone-warn", running: "tone-warn",
  draft: "tone-muted", failed: "tone-danger",
};

export function ReportsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [task, setTask] = useState("");
  const [status, setStatus] = useState("");
  const [mode, setMode] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const query = useQuery({
    queryKey: ["catalog", "reports"],
    queryFn: () => fetchProductCatalog("reports"),
  });

  const rows = useMemo(
    () => ((query.data?.items ?? []) as unknown as CatalogReport[]).map(toRow),
    [query.data],
  );

  const filtered = useMemo(() => rows.filter((row) => (
    (!task || row.taskName === task)
    && (!status || row.status === status)
    && (!mode || row.mode === mode)
  )), [rows, task, status, mode]);

  const visible = usePage(filtered, pageSize, page);
  const tasks = [...new Set(rows.map((row) => row.taskName))];
  const statuses = [...new Set(rows.map((row) => row.status))];
  const modes = [...new Set(rows.map((row) => row.mode).filter((value): value is string => !!value))];

  const columns: Array<Column<ReportRow>> = [
    { id: "title", header: "报告名称", render: (row) => <strong>{row.title}</strong> },
    { id: "task", header: "任务", render: (row) => <span className="cell-muted">{row.taskName}</span> },
    { id: "mode", header: "模式", render: (row) => row.mode ? <span className="cell-muted">{modeLabel(row.mode)}</span> : dash() },
    { id: "version", header: "版本", render: (row) => row.version ?? dash() },
    { id: "status", header: "状态", render: (row) => <span className={`ref-chip ${STATUS_TONES[row.status] ?? "tone-muted"}`}>{row.status}</span> },
    { id: "findings", header: "Findings", render: (row) => row.findings === null ? dash() : row.findings },
    { id: "updated", header: "生成时间", render: (row) => <span className="cell-muted">{row.updatedAt}</span> },
    {
      id: "actions",
      header: "操作",
      render: (row) => <span className="row-actions">
        <button
          className="ref-link-button"
          onClick={(event) => {
            event.stopPropagation();
            navigate(`/tasks/${encodeURIComponent(row.taskId)}`);
          }}
        >查看</button>
        <button
          className="ref-secondary-button"
          onClick={(event) => {
            event.stopPropagation();
            // The report endpoint streams Markdown for the owning task.
            window.open(reportUrl(row.taskId), "_blank", "noopener");
          }}
        ><Download size={13} />导出</button>
      </span>,
    },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>报告中心</h1>
        <p>查看和管理所有安全报告</p>
      </div>
      <button className="ref-primary-button" onClick={() => toast.notifyUnavailable("新建报告")}>
        <Plus size={16} />新建报告
      </button>
    </header>

    <section className="ref-filter-row" aria-label="筛选报告">
      <select aria-label="任务筛选" value={task} onChange={(event) => { setTask(event.target.value); setPage(1); }}>
        <option value="">所有任务</option>
        {tasks.map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
      <select aria-label="状态筛选" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
        <option value="">所有状态</option>
        {statuses.map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
      <select aria-label="模式筛选" value={mode} onChange={(event) => { setMode(event.target.value); setPage(1); }}>
        <option value="">所有模式</option>
        {modes.map((value) => <option key={value} value={value}>{modeLabel(value)}</option>)}
      </select>
    </section>

    {query.isLoading ? <LoadingSkeleton label="正在读取报告目录" rows={5} />
      : query.isError ? <ErrorState
        description={query.error instanceof Error ? query.error.message : "无法读取报告目录"}
        actionLabel="重试"
        onAction={() => void query.refetch()}
      />
      : <>
        <CatalogTable fill columns={columns} rows={visible} rowKey={(row) => row.id} label="报告列表" emptyLabel="没有匹配的报告" />
        <Pagination total={filtered.length} pageSize={pageSize} page={page} onPage={setPage} onPageSize={(size) => { setPageSize(size); setPage(1); }} />
      </>}
  </div>;
}

function toRow(item: CatalogReport): ReportRow {
  return {
    id: item.id,
    taskId: item.task_id,
    title: item.title,
    taskName: item.task_id,
    // The catalog carries no mode, version or finding count for an export.
    mode: null,
    version: null,
    status: item.status,
    findings: null,
    updatedAt: formatEpoch(item.updated_at),
  };
}

function dash() {
  return <span className="field-empty">—</span>;
}

function modeLabel(mode: string): string {
  return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode;
}

function formatEpoch(value: number): string {
  if (!value) return "—";
  // The catalog returns a POSIX mtime in seconds.
  return new Date(value * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
