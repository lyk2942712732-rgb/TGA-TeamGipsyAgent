import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { requestJson } from "../api/client";
import { runtimeApi } from "../runtime/api-v2";
import type { MCPManagedServer } from "../runtime/event-types";
import { MCPWizard } from "../components/mcp/MCPWizard";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { CatalogTable, type Column } from "../components/ui/CatalogTable";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { ChipList, FieldGrid } from "../components/ui/FieldGrid";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { RiskBadge } from "../components/ui/RiskBadge";
import { StatusBadge } from "../shared/StatusBadge";
import { MODE_PROFILES } from "../modes";

type Capability = {
  name: string;
  description: string;
  kind: string;
  risk: string;
  modes: string[];
  availability: string;
  budget_key?: string;
  input_schema?: { properties?: Record<string, unknown>; required?: string[] };
};

type McpRecord = {
  server: string;
  configured: boolean;
  enabled: boolean;
  reachable: boolean;
  discovered: boolean;
  tools: number;
  transport: string;
  image?: string | null;
  endpoint?: string | null;
  error?: string | null;
  protocol_version?: string;
};

const TABS: DetailTab[] = [
  { id: "capabilities", label: "Capabilities（工具能力）" },
  { id: "servers", label: "MCP Servers（服务器）" },
];

const fetchCapabilitySnapshot = () => requestJson<{ capabilities: Capability[] }>("/api/v2/capabilities");
const fetchToolHealth = () => requestJson<{ configured: boolean; records: McpRecord[] }>("/api/v2/tools/health");

export function CapabilitiesPage() {
  const [tab, setTab] = useState("capabilities");

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>Tools &amp; MCP</h1>
        <p>管理工具能力和 MCP 服务器</p>
      </div>
    </header>

    <DetailTabs tabs={TABS} active={tab} onSelect={setTab} size="lg" />

    {tab === "capabilities" ? <CapabilitiesTab /> : <ServersTab />}
  </div>;
}

/** The registry's own `kind`, presented as the reference's 类别 / 执行位置. */
const KIND_LABELS: Record<string, string> = {
  workspace: "Workspace", http: "Network", control: "Control", execution: "Execution",
};
const SURFACE_LABELS: Record<string, string> = {
  workspace: "任务工作区", http: "受控网络出口", control: "编排运行时", execution: "隔离容器",
};

function CapabilitiesTab() {
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [selectedName, setSelectedName] = useState<string | null>(null);

  const query = useQuery({ queryKey: ["capabilities"], queryFn: fetchCapabilitySnapshot });
  const all = useMemo(() => query.data?.capabilities ?? [], [query.data]);

  const items = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return all.filter((item) => (!needle
      || item.name.toLocaleLowerCase().includes(needle)
      || item.description.toLocaleLowerCase().includes(needle))
      && (!kind || item.kind === kind));
  }, [all, search, kind]);

  const selected = items.find((item) => item.name === selectedName) ?? items[0] ?? null;

  const columns: Array<Column<Capability>> = [
    { id: "name", header: "能力名称", render: (row) => <strong className="capability-name">{row.name}</strong> },
    { id: "kind", header: "类别", render: (row) => <span className="cell-muted">{KIND_LABELS[row.kind] ?? row.kind}</span> },
    { id: "risk", header: "风险等级", render: (row) => <RiskBadge value={row.risk} /> },
    // Approval is decided by the task's ExecutionPolicy.high_impact mode, not by
    // the capability — so this cannot be derived from `risk` here.
    { id: "approval", header: "审批要求", render: () => <span className="ref-chip tone-muted">按任务策略</span> },
    { id: "where", header: "执行位置", render: (row) => <span className="cell-muted">{SURFACE_LABELS[row.kind] ?? "—"}</span> },
  ];

  if (query.isLoading) return <LoadingSkeleton label="正在读取能力目录" rows={6} />;
  if (query.isError) return <ErrorState
    description={query.error instanceof Error ? query.error.message : "无法读取 Capability Registry"}
    actionLabel="重试"
    onAction={() => void query.refetch()}
  />;

  return <>
    <section className="ref-filter-row" aria-label="筛选能力">
      <label className="ref-search">
        <Search size={16} aria-hidden="true" />
        <input aria-label="搜索能力名称" placeholder="搜索能力名称..."
          value={search} onChange={(event) => setSearch(event.target.value)} />
      </label>
      <select aria-label="类别筛选" value={kind} onChange={(event) => setKind(event.target.value)}>
        <option value="">类别: 全部</option>
        {[...new Set(all.map((item) => item.kind))].sort().map((value) => (
          <option key={value} value={value}>{value}</option>
        ))}
      </select>
    </section>

    {!items.length ? <EmptyState title="没有匹配的能力" description="调整搜索或筛选条件后重试。" />
      : <div className="ref-master-detail tools-layout ref-fill">
        <CatalogTable
          fill
          columns={columns}
          rows={items}
          rowKey={(row) => row.name}
          selectedKey={selected?.name}
          onSelect={(row) => setSelectedName(row.name)}
        />
        {selected ? <section className="ref-detail-panel" aria-label={`${selected.name} 详情`}>
          <header className="ref-detail-head">
            <div className="ref-detail-title"><h2>{selected.name}</h2></div>
            <StatusBadge value={selected.availability} />
          </header>

          <FieldGrid fields={[
            { label: "类别", value: KIND_LABELS[selected.kind] ?? selected.kind },
            { label: "风险等级", value: <RiskBadge value={selected.risk} /> },
            { label: "审批要求", value: <span className="ref-chip tone-muted">按任务策略</span> },
            { label: "执行位置", value: SURFACE_LABELS[selected.kind], missing: !SURFACE_LABELS[selected.kind] },
            { label: "预算科目", value: selected.budget_key },
            { label: "适用模式", value: <ChipList values={selected.modes.map(modeLabel)} tone="neutral" /> },
          ]} />

          <div>
            <h3 className="ref-subhead">描述</h3>
            <p className="skill-summary">{selected.description}</p>
          </div>

          <div>
            <h3 className="ref-subhead">参数模式</h3>
            <ChipList values={Object.keys(selected.input_schema?.properties ?? {})} />
          </div>

          <div>
            <h3 className="ref-subhead">使用统计</h3>
            <FieldGrid fields={[
              { label: "调用次数", missing: true },
              { label: "关联任务数", missing: true },
              { label: "更新时间", missing: true },
            ]} />
          </div>
        </section> : null}
      </div>}
  </>;
}

function ServersTab() {
  const client = useQueryClient();
  const [selectedServer, setSelectedServer] = useState<string | null>(null);
  const [wizardFor, setWizardFor] = useState<MCPManagedServer | null | "new">(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const query = useQuery({ queryKey: ["tools", "health"], queryFn: fetchToolHealth });
  const managed = useQuery({ queryKey: ["mcp", "servers"], queryFn: () => runtimeApi.mcpServers() });

  const records = query.data?.records ?? [];
  const selected = records.find((item) => item.server === selectedServer) ?? records[0] ?? null;
  const managedFor = (id: string) => managed.data?.servers.find((item) => item.id === id) ?? null;

  const refreshAll = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["tools", "health"] }),
      client.invalidateQueries({ queryKey: ["mcp", "servers"] }),
    ]);
  };

  const toggleEnabled = async (id: string, enabled: boolean) => {
    setBusy(true);
    setError("");
    try {
      await runtimeApi.updateMCPServer(id, { enabled });
      await refreshAll();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新 MCP 服务器失败");
    } finally {
      setBusy(false);
    }
  };

  const removeServer = async (id: string) => {
    setBusy(true);
    setError("");
    try {
      await runtimeApi.deleteMCPServer(id);
      await refreshAll();
      setConfirmDelete(null);
      setSelectedServer(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除 MCP 服务器失败");
      setConfirmDelete(null);
    } finally {
      setBusy(false);
    }
  };

  const columns: Array<Column<McpRecord>> = [
    { id: "server", header: "服务器", render: (row) => <strong>{row.server}</strong> },
    { id: "transport", header: "传输", render: (row) => <span className="chip tone-neutral">{row.transport}</span> },
    {
      id: "enabled", header: "启用",
      render: (row) => <StatusBadge value={row.enabled ? "available" : "unavailable"} label={row.enabled ? "已启用" : "未启用"} />,
    },
    {
      id: "reachable", header: "可达",
      render: (row) => <StatusBadge value={row.reachable ? "healthy" : "unavailable"} label={row.reachable ? "可达" : "不可达"} />,
    },
    { id: "tools", header: "工具数", render: (row) => row.tools, align: "center" },
    { id: "image", header: "镜像 / 端点", render: (row) => <code className="ref-hash">{row.image ?? row.endpoint ?? "—"}</code> },
  ];

  if (query.isLoading) return <LoadingSkeleton label="正在读取 MCP 健康状态" rows={6} />;
  if (query.isError) return <ErrorState
    description={query.error instanceof Error ? query.error.message : "无法读取 MCP 健康快照"}
    actionLabel="重试"
    onAction={() => void query.refetch()}
  />;

  const toolbar = <section className="ref-filter-row" aria-label="MCP 操作">
    <button className="ref-primary-button" onClick={() => setWizardFor("new")}><Plus size={16} />添加 MCP 服务器</button>
    <button className="ref-secondary-button" disabled={busy} onClick={() => void refreshAll()}><RefreshCw size={14} />刷新</button>
  </section>;

  const wizard = wizardFor !== null ? <MCPWizard
    initial={wizardFor === "new" ? undefined : wizardFor}
    onClose={() => setWizardFor(null)}
    onSaved={() => { setWizardFor(null); void refreshAll(); }}
  /> : null;

  if (!records.length) return <>
    {toolbar}
    {error ? <p className="inline-error" role="alert">{error}</p> : null}
    <EmptyState title="暂无 MCP 服务器" description="通过「添加 MCP 服务器」导入镜像，或在 config/mcp.json 中配置。" />
    {wizard}
  </>;

  return <>
    {toolbar}
    {error ? <p className="inline-error" role="alert">{error}</p> : null}

    <div className="ref-master-detail tools-layout">
      <CatalogTable
        columns={columns}
        rows={records}
        rowKey={(row) => row.server}
        selectedKey={selected?.server}
        onSelect={(row) => setSelectedServer(row.server)}
      />
      {selected ? <section className="ref-detail-panel" aria-label={`${selected.server} 详情`}>
        <header className="ref-detail-head">
          <div className="ref-detail-title"><h2>{selected.server}</h2></div>
          <div className="policy-actions">
            <button className="ref-secondary-button" disabled={busy}
              onClick={() => void toggleEnabled(selected.server, !selected.enabled)}>
              {selected.enabled ? "停用" : "启用"}
            </button>
            {managedFor(selected.server)
              ? <>
                <button className="ref-secondary-button" disabled={busy}
                  onClick={() => setWizardFor(managedFor(selected.server))}>编辑</button>
                <button className="ref-secondary-button" disabled={busy}
                  onClick={() => setConfirmDelete(selected.server)}><Trash2 size={14} />删除</button>
              </>
              : null}
          </div>
        </header>

        <FieldGrid columns={2} fields={[
          { label: "已配置", value: selected.configured ? "是" : "否" },
          { label: "已启用", value: selected.enabled ? "是" : "否" },
          { label: "已发现", value: selected.discovered ? "是" : "否" },
          { label: "传输方式", value: selected.transport },
          { label: "工具数", value: selected.tools },
          { label: "协议版本", value: selected.protocol_version || <span className="field-empty">—</span> },
          { label: "镜像", value: selected.image ?? <span className="field-empty">—</span> },
          { label: "端点", value: selected.endpoint ?? <span className="field-empty">—</span> },
        ]} />

        {selected.error ? <p className="inline-error" role="alert">{selected.error}</p> : null}
      </section> : null}
    </div>

    <ConfirmDialog
      open={confirmDelete !== null}
      title={`删除 MCP 服务器 ${confirmDelete ?? ""}`}
      description="删除后该服务器不再出现在工具目录中。已导入的镜像不会被删除。"
      confirmLabel="删除"
      danger
      busy={busy}
      onConfirm={() => confirmDelete && void removeServer(confirmDelete)}
      onCancel={() => setConfirmDelete(null)}
    />

    {wizard}
  </>;
}

function modeLabel(mode: string): string {
  return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode;
}
