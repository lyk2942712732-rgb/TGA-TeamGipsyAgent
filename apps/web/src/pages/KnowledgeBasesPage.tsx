import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { fetchProductCatalog } from "../api/catalog-query-adapter";
import { CatalogTable, Pagination, usePage, type Column } from "../components/ui/CatalogTable";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { useToast } from "../components/ui/Toast";

/**
 * 知识库.
 *
 * `/api/v2/catalog/knowledge-bases` projects any `knowledge_bases` rows a task
 * database happens to carry.  No task writes that table yet, so the list is
 * normally empty; it renders whatever the catalog returns rather than a
 * fabricated roster.  Index totals and sync history have no endpoint at all,
 * so those cards are not rendered.
 */

type KnowledgeBaseRow = {
  id: string;
  name: string;
  type: string;
  scope: string;
  documents: number | null;
  sources: number | null;
  indexVersion: string;
  status: string;
  lastSync: string;
};

const TABS: DetailTab[] = [
  { id: "bases", label: "知识库" },
  { id: "sources", label: "Sources", missing: true },
  { id: "documents", label: "Documents", missing: true },
  { id: "snapshots", label: "Index Snapshots", missing: true },
  { id: "search", label: "检索测试", missing: true },
];

export function KnowledgeBasesPage() {
  const toast = useToast();
  const [tab, setTab] = useState("bases");
  const [selected, setSelected] = useState<string>("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const query = useQuery({
    queryKey: ["catalog", "knowledge-bases"],
    queryFn: () => fetchProductCatalog("knowledge-bases"),
  });
  const rows = (query.data?.items ?? []).map(toRow);
  const visible = usePage(rows, pageSize, page);

  const columns: Array<Column<KnowledgeBaseRow>> = [
    { id: "name", header: "名称", render: (row) => <strong>{row.name}</strong> },
    { id: "type", header: "类型", render: (row) => <span className="cell-muted">{row.type}</span> },
    { id: "scope", header: "Scope", render: (row) => <span className="ref-chip tone-muted">{row.scope}</span> },
    { id: "documents", header: "文档数", render: (row) => row.documents === null ? dash() : row.documents.toLocaleString(), align: "end" },
    { id: "sources", header: "Source 数", render: (row) => row.sources === null ? dash() : row.sources, align: "end" },
    { id: "index", header: "索引版本", render: (row) => <code className="cell-mono">{row.indexVersion}</code> },
    { id: "status", header: "状态", render: (row) => <span className="ref-chip tone-ok">{row.status}</span> },
    { id: "sync", header: "最后同步", render: (row) => <span className="cell-muted">{row.lastSync}</span> },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>知识库</h1>
        <p>管理知识库和索引</p>
      </div>
      <button className="ref-primary-button" onClick={() => toast.notifyUnavailable("新建知识库")}>
        <Plus size={16} />新建知识库
      </button>
    </header>

    <DetailTabs tabs={TABS} active={tab} onSelect={setTab} size="lg" />

    {tab !== "bases" ? <EmptyState label={`暂无${TABS.find((item) => item.id === tab)?.label}数据`} />
      : query.isLoading ? <LoadingSkeleton label="正在读取知识库目录" rows={5} />
        : query.isError ? <ErrorState
          description={query.error instanceof Error ? query.error.message : "无法读取知识库目录"}
          actionLabel="重试"
          onAction={() => void query.refetch()}
        />
        : <>
          <CatalogTable
            fill
            columns={columns}
            rows={visible}
            rowKey={(row) => row.id}
            selectedKey={selected}
            onSelect={(row) => setSelected(row.id)}
            label="知识库列表"
            emptyLabel="暂无知识库"
          />
          <Pagination total={rows.length} pageSize={pageSize} page={page} onPage={setPage} onPageSize={(size) => { setPageSize(size); setPage(1); }} />
        </>}
  </div>;
}

function toRow(item: Record<string, unknown>): KnowledgeBaseRow {
  const id = String(item.id ?? item.knowledge_base_id ?? "");
  return {
    id,
    name: String(item.name ?? item.title ?? id),
    type: text(item.type) ?? "—",
    scope: text(item.scope) ?? "—",
    documents: count(item.document_count ?? item.documents),
    sources: count(item.source_count ?? item.sources),
    indexVersion: text(item.index_version) ?? "—",
    status: text(item.status) ?? "—",
    lastSync: text(item.last_sync_at) ?? "—",
  };
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function dash() {
  return <span className="field-empty">—</span>;
}
