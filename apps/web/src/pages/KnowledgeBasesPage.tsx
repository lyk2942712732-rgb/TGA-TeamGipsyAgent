import { Database, FileText, Hash, Plus, PieChart } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";
import { CatalogTable, Pagination, usePage, type Column } from "../components/ui/CatalogTable";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { EmptyState } from "../components/ui/EmptyState";
import { useToast } from "../components/ui/Toast";

/**
 * 知识库 (reference image 09).
 *
 * Design skeleton only: schema_v6 has no `knowledge_bases` table and
 * `/api/v2/catalog/knowledge-bases` is hard-wired to return an empty set, so
 * every row, counter and sync record below is illustrative.  The in-product
 * "样例" markers were removed on request — this page is listed in the handover
 * notes as sample-only so the gap stays visible off-screen.
 */

type KnowledgeBaseRow = {
  id: string;
  name: string;
  type: string;
  scope: string;
  documents: number;
  sources: number;
  indexVersion: string;
  status: string;
  lastSync: string;
};

const SAMPLE_ROWS: KnowledgeBaseRow[] = [
  { id: "kb_public_sec", name: "官方安全文档库", type: "Global Public", scope: "global", documents: 1248, sources: 5, indexVersion: "v3.2.1", status: "正常", lastSync: "今天 08:15" },
  { id: "kb_org_policy", name: "组织安全隐患库", type: "Workspace Private", scope: "workspace", documents: 356, sources: 3, indexVersion: "v1.4.0", status: "正常", lastSync: "昨天 23:45" },
  { id: "kb_tool_docs", name: "工具文档库", type: "Tool Documentation", scope: "global", documents: 872, sources: 12, indexVersion: "v2.8.3", status: "正常", lastSync: "今天 07:30" },
  { id: "kb_history", name: "历史案例库", type: "Curated Historical", scope: "workspace", documents: 215, sources: 2, indexVersion: "v1.1.0", status: "正常", lastSync: "5-19 18:20" },
];

const SAMPLE_SYNC = [
  { id: "kb_public_sec", name: "官方安全文档库", result: "成功", at: "今天 08:15" },
  { id: "kb_tool_docs", name: "工具文档库", result: "成功", at: "昨天 07:30" },
  { id: "kb_org_policy", name: "组织安全隐患库", result: "成功", at: "昨天 23:45" },
];

const STATS: Array<{ label: string; value: string; icon: LucideIcon; tone: string }> = [
  { label: "总知识库", value: "4", icon: Database, tone: "tone-info" },
  { label: "总文档", value: "2,691", icon: FileText, tone: "tone-info" },
  { label: "总Chunks", value: "18,432", icon: Hash, tone: "tone-violet" },
  { label: "索引大小", value: "2.4 GB", icon: PieChart, tone: "tone-ok" },
];

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
  const [selected, setSelected] = useState<string>(SAMPLE_ROWS[0].id);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const visible = usePage(SAMPLE_ROWS, pageSize, page);

  const columns: Array<Column<KnowledgeBaseRow>> = [
    { id: "name", header: "名称", render: (row) => <strong>{row.name}</strong> },
    { id: "type", header: "类型", render: (row) => <span className="cell-muted">{row.type}</span> },
    { id: "scope", header: "Scope", render: (row) => <span className="ref-chip tone-muted">{row.scope}</span> },
    { id: "documents", header: "文档数", render: (row) => row.documents.toLocaleString(), align: "end" },
    { id: "sources", header: "Source 数", render: (row) => row.sources, align: "end" },
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

    {tab === "bases" ? <>
      <CatalogTable
        columns={columns}
        rows={visible}
        rowKey={(row) => row.id}
        selectedKey={selected}
        onSelect={(row) => setSelected(row.id)}
        label="知识库列表"
      />
      <Pagination total={SAMPLE_ROWS.length} pageSize={pageSize} page={page} onPage={setPage} onPageSize={(size) => { setPageSize(size); setPage(1); }} />

      <div className="kb-bottom-grid ref-fill">
        <section className="ref-card">
          <header className="ref-card-head"><h2>索引状态</h2></header>
          <div className="kb-stats">
            {STATS.map((stat) => <article key={stat.label}>
              <span className={`row-icon ${stat.tone}`} aria-hidden="true"><stat.icon size={16} /></span>
              <small>{stat.label}</small>
              <strong>{stat.value}</strong>
            </article>)}
          </div>
        </section>

        <section className="ref-card">
          <header className="ref-card-head"><h2>最近同步</h2></header>
          <ul className="kb-sync-list">
            {SAMPLE_SYNC.map((row) => <li key={row.id}>
              <span className="row-icon tone-info" aria-hidden="true"><FileText size={15} /></span>
              <strong className="ellipsis">{row.name}</strong>
              <span className="ref-chip tone-ok">{row.result}</span>
              <time>{row.at}</time>
            </li>)}
          </ul>
          <footer className="card-footer-link">
            <button className="ref-link-button" onClick={() => toast.notifyUnavailable("同步记录")}>查看全部同步记录 ›</button>
          </footer>
        </section>
      </div>
    </> : <EmptyState label={`暂无${TABS.find((item) => item.id === tab)?.label}数据`} />}
  </div>;
}
