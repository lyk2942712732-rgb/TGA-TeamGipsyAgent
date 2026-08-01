import { useQuery } from "@tanstack/react-query";
import { Search, Upload } from "lucide-react";
import { useMemo, useState } from "react";
import { fetchProductCatalog } from "../api/catalog-query-adapter";
import { CatalogTable, Pagination, usePage, type Column } from "../components/ui/CatalogTable";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { useToast } from "../components/ui/Toast";

/**
 * 资源中心 (reference image 07).
 *
 * `/api/v2/catalog/resources` projects a task's Artifact / EvidenceClaim /
 * Finding rows.  It carries no byte size, so 大小 shows a dash.  The Knowledge
 * tab is projected from the same catalog and stays empty until a task persists
 * knowledge items.
 */

type ResourceRow = {
  id: string;
  kind: string;
  name: string;
  type: string;
  taskName: string;
  solver: string | null;
  size: string | null;
  hash: string | null;
  createdAt: string;
  status: string;
};

type CatalogResource = {
  id: string;
  task_id: string;
  kind: string;
  title: string;
  status: string | null;
  raw: Record<string, unknown>;
};

const TABS: DetailTab[] = [
  { id: "artifacts", label: "Artifacts" },
  { id: "evidence", label: "Evidence Claims" },
  { id: "findings", label: "Findings" },
  { id: "knowledge", label: "Knowledge" },
];

const TYPE_TONES: Record<string, string> = {
  JSON: "tone-info", SQL: "tone-violet", XML: "tone-ok", ZIP: "tone-warn", RAW: "tone-info",
  High: "tone-danger", Medium: "tone-warn", Low: "tone-ok",
  Claim: "tone-info", Doc: "tone-muted",
};
const STATUS_TONES: Record<string, string> = {
  已确认: "tone-ok", 已入库: "tone-ok", 待复核: "tone-warn", 已驳回: "tone-danger",
};

export function ResourcesPage() {
  const toast = useToast();
  const [tab, setTab] = useState("artifacts");
  const [search, setSearch] = useState("");
  const [taskName, setTaskName] = useState("");
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const query = useQuery({
    queryKey: ["catalog", "resources"],
    queryFn: () => fetchProductCatalog("resources"),
  });

  const rows = useMemo(
    () => ((query.data?.items ?? []) as unknown as CatalogResource[])
      .filter((item) => item.kind === tab)
      .map(toRow),
    [query.data, tab],
  );

  const filtered = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return rows.filter((row) => (
      (!needle || row.name.toLocaleLowerCase().includes(needle))
      && (!taskName || row.taskName === taskName)
      && (!type || row.type === type)
      && (!status || row.status === status)
    ));
  }, [rows, search, taskName, type, status]);

  const visible = usePage(filtered, pageSize, page);
  const tasks = [...new Set(rows.map((row) => row.taskName))];
  const types = [...new Set(rows.map((row) => row.type))];
  const statuses = [...new Set(rows.map((row) => row.status))];

  const columns: Array<Column<ResourceRow>> = [
    {
      id: "name", header: "文件名",
      render: (row) => <span className="cell-with-icon">
        <span className={`file-badge ${TYPE_TONES[row.type] ?? "tone-muted"}`} aria-hidden="true">{row.type.slice(0, 4)}</span>
        <strong className="ellipsis">{row.name}</strong>
      </span>,
    },
    { id: "type", header: "类型", render: (row) => <span className="cell-muted">{row.type}</span> },
    { id: "task", header: "来源任务", render: (row) => <span className="cell-muted">{row.taskName}</span> },
    { id: "solver", header: "来源 Solver", render: (row) => row.solver ? <span className="cell-muted">{row.solver}</span> : dash() },
    { id: "size", header: "大小", render: (row) => row.size ? <span className="cell-muted">{row.size}</span> : dash() },
    { id: "hash", header: "Hash", render: (row) => row.hash ? <code className="cell-mono">{row.hash.slice(0, 10)}…</code> : dash() },
    { id: "created", header: "创建时间", render: (row) => <span className="cell-muted">{row.createdAt}</span> },
    { id: "status", header: "状态", render: (row) => <span className={`ref-chip ${STATUS_TONES[row.status] ?? "tone-muted"}`}>{row.status}</span> },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>资源中心</h1>
        <p>所有任务的证据和工作产物</p>
      </div>
    </header>

    <DetailTabs tabs={TABS} active={tab} onSelect={(id) => { setTab(id); setPage(1); }} size="lg" />

    <section className="ref-filter-row" aria-label="筛选资源">
      <select aria-label="任务筛选" value={taskName} onChange={(event) => { setTaskName(event.target.value); setPage(1); }}>
        <option value="">所有任务</option>
        {tasks.map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
      <select aria-label="类型筛选" value={type} onChange={(event) => { setType(event.target.value); setPage(1); }}>
        <option value="">所有类型</option>
        {types.map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
      <select aria-label="状态筛选" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
        <option value="">所有状态</option>
        {statuses.map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
      <label className="ref-search">
        <Search size={16} aria-hidden="true" />
        <input
          aria-label="搜索文件名或内容"
          placeholder="搜索文件名或内容..."
          value={search}
          onChange={(event) => { setSearch(event.target.value); setPage(1); }}
        />
      </label>
      <button className="ref-primary-button push-end" onClick={() => toast.notifyUnavailable("上传资源")}>
        <Upload size={16} />上传
      </button>
    </section>

    {query.isLoading ? <LoadingSkeleton label="正在读取资源目录" rows={6} />
      : query.isError ? <ErrorState
        description={query.error instanceof Error ? query.error.message : "无法读取资源目录"}
        actionLabel="重试"
        onAction={() => void query.refetch()}
      />
      : <>
        <CatalogTable fill columns={columns} rows={visible} rowKey={(row) => row.id} label="资源列表" emptyLabel="没有匹配的资源" />
        <Pagination total={filtered.length} pageSize={pageSize} page={page} onPage={setPage} onPageSize={(size) => { setPageSize(size); setPage(1); }} />
      </>}
  </div>;
}

function toRow(item: CatalogResource): ResourceRow {
  const raw = item.raw ?? {};
  return {
    id: item.id,
    kind: item.kind,
    name: item.title,
    type: text(raw.media_type) ?? text(raw.kind) ?? "—",
    taskName: item.task_id,
    solver: text(raw.source_solver_id),
    // The resource projection carries no byte size.
    size: null,
    hash: text(raw.sha256),
    createdAt: formatDate(text(raw.created_at)),
    status: item.status ?? "—",
  };
}

function dash() {
  return <span className="field-empty">—</span>;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
