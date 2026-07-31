import { useQuery } from "@tanstack/react-query";
import { Search, Upload } from "lucide-react";
import { useMemo, useState } from "react";
import { fetchProductCatalog } from "../api/catalog-query-adapter";
import { CatalogTable, Pagination, usePage, type Column } from "../components/ui/CatalogTable";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { useToast } from "../components/ui/Toast";
import { padRows } from "./sample";

/**
 * 资源中心 (reference image 07).
 *
 * `/api/v2/catalog/resources` projects a task's Artifact / EvidenceClaim /
 * Finding rows.  It carries no byte size, so 大小 shows a dash on real rows.
 * Knowledge has no list endpoint at all — that tab is sample-only.
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
  sample: boolean;
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

const SAMPLE_ROWS: Record<string, ResourceRow[]> = {
  artifacts: [
    sample("s-a1", "artifacts", "response_admin_20240520.json", "JSON", "Web API 安全测试", "Web Analyst", "12.3 KB", "a1b2c3d4e5f6a7b8", "10:45:12", "已确认"),
    sample("s-a2", "artifacts", "inject_payload.sql", "SQL", "内网渗透评估", "Web Analyst", "2.1 KB", "d4e5f6g7h8i9j0k1", "10:30:45", "已确认"),
    sample("s-a3", "artifacts", "port_scan_result.xml", "XML", "Web API 安全测试", "Recon Worker", "87 KB", "h1i2j3k4l5m6n7o8", "10:28:33", "已确认"),
    sample("s-a4", "artifacts", "app_source_code.zip", "ZIP", "代码审查分析", "Code Auditor", "452 MB", "k6l7m8n9o0p1q2r3", "09:16:22", "已入库"),
    sample("s-a5", "artifacts", "memory_dump.raw", "RAW", "应急响应分析", "IR Analyst", "2.1 GB", "p3q4r5s6t7u8v9w0", "昨天 18:22", "已确认"),
  ],
  evidence: [
    sample("s-e1", "evidence", "IDOR 越权访问证据", "Claim", "Web API 安全测试", "Web Analyst", "4.8 KB", "b2c3d4e5f6a7b8c9", "10:52:04", "已确认"),
    sample("s-e2", "evidence", "SQL 报错回显证据", "Claim", "内网渗透评估", "Web Analyst", "3.2 KB", "c3d4e5f6a7b8c9d0", "10:41:18", "已确认"),
    sample("s-e3", "evidence", "堆栈信息泄露证据", "Claim", "Web API 安全测试", "Web Analyst", "1.9 KB", "e5f6a7b8c9d0e1f2", "09:47:36", "待复核"),
  ],
  findings: [
    sample("s-f1", "findings", "未授权访问：水平越权（IDOR）", "High", "Web API 安全测试", "Evidence Reviewer", "—", "f6a7b8c9d0e1f2a3", "10:24:11", "已确认"),
    sample("s-f2", "findings", "敏感信息泄露：错误信息包含堆栈跟踪", "Medium", "Web API 安全测试", "Evidence Reviewer", "—", "a7b8c9d0e1f2a3b4", "09:47:52", "已确认"),
    sample("s-f3", "findings", "安全响应头缺失：X-Content-Type-Options", "Low", "Web API 安全测试", "Evidence Reviewer", "—", "b8c9d0e1f2a3b4c5", "08:32:07", "待复核"),
  ],
  knowledge: [
    sample("s-k1", "knowledge", "OWASP API Top 10 摘要", "Doc", "官方安全文档库", null, "128 KB", "c9d0e1f2a3b4c5d6", "今天 08:15", "已入库"),
    sample("s-k2", "knowledge", "内网横向移动手法索引", "Doc", "历史案例库", null, "64 KB", "d0e1f2a3b4c5d6e7", "昨天 23:45", "已入库"),
  ],
};

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

  const rows = useMemo(() => {
    const real = ((query.data?.items ?? []) as unknown as CatalogResource[])
      .filter((item) => item.kind === tab)
      .map(toRow);
    return padRows(real, SAMPLE_ROWS[tab] ?? [], (SAMPLE_ROWS[tab] ?? []).length, (row) => row.name);
  }, [query.data, tab]);

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
    sample: false,
  };
}

function sample(
  id: string, kind: string, name: string, type: string, taskName: string,
  solver: string | null, size: string, hash: string, createdAt: string, status: string,
): ResourceRow {
  return { id, kind, name, type, taskName, solver, size: size === "—" ? null : size, hash, createdAt, status, sample: true };
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
