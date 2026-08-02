import { useQuery } from "@tanstack/react-query";
import { Info, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { fetchExecutionPolicies, type ExecutionPolicyRecord } from "../api/catalog-query-adapter";
import { CatalogTable, type Column } from "../components/ui/CatalogTable";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { FieldGrid } from "../components/ui/FieldGrid";

/**
 * 策略与预算.
 *
 * `/api/v2/catalog/policies` projects the immutable ExecutionPolicy preset each
 * task mode freezes at creation time.  Every value on this page is read from
 * that record; there is no editable policy store, so nothing here is writable
 * and no rule text is authored in the frontend.
 */

const PRESET_LABELS: Record<string, string> = {
  autonomous_ctf: "自主解题",
  safe_observation: "安全观察",
  offline_analysis: "离线分析",
  custom: "自定义",
};

const NETWORK_ACCESS_LABELS: Record<string, string> = {
  disabled: "禁用",
  task_sources: "仅任务来源",
  public_internet: "公网",
  custom: "自定义",
};

const INTERACTION_LABELS: Record<string, string> = { observe: "只读观察", interact: "允许交互" };
const COMPUTE_LABELS: Record<string, string> = { disabled: "禁用", isolated: "隔离容器" };
const HIGH_IMPACT_LABELS: Record<string, string> = {
  forbidden: "禁止",
  approval_required: "需审批",
  allowlisted: "白名单放行",
};

const TABS: DetailTab[] = [
  { id: "execution", label: "执行策略" },
  { id: "tools", label: "工具策略", missing: true },
  { id: "budgets", label: "预算模板", missing: true },
  { id: "retention", label: "保留策略", missing: true },
];

export function PoliciesPage() {
  const [tab, setTab] = useState("execution");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState("");

  const query = useQuery({ queryKey: ["catalog", "policies"], queryFn: () => fetchExecutionPolicies() });

  const rows = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return (query.data?.items ?? []).filter((row) => !needle
      || row.id.toLocaleLowerCase().includes(needle)
      || row.mode.toLocaleLowerCase().includes(needle)
      || row.mode_label.toLocaleLowerCase().includes(needle)
      || row.preset.toLocaleLowerCase().includes(needle));
  }, [query.data, search]);

  const selected = rows.find((row) => row.id === selectedId) ?? rows[0] ?? null;

  const columns: Array<Column<ExecutionPolicyRecord>> = [
    { id: "name", header: "名称", render: (row) => <strong className="policy-name">{row.id}</strong> },
    { id: "modes", header: "适用模式", render: (row) => <span className="cell-muted">{row.mode_label}</span> },
    { id: "preset", header: "预设", render: (row) => <span className="ref-chip tone-info">{PRESET_LABELS[row.preset] ?? row.preset}</span> },
    {
      id: "network", header: "网络",
      render: (row) => <span className="cell-muted">
        {NETWORK_ACCESS_LABELS[row.execution_policy.network.access] ?? row.execution_policy.network.access}
        {" · "}
        {INTERACTION_LABELS[row.execution_policy.network.interaction] ?? row.execution_policy.network.interaction}
      </span>,
    },
    {
      id: "impact", header: "高影响",
      render: (row) => {
        const mode = row.execution_policy.high_impact.mode;
        const label = HIGH_IMPACT_LABELS[mode] ?? mode;
        return <span className={mode === "approval_required" ? "policy-warn" : ""}>{label}</span>;
      },
    },
    // The catalog only publishes presets the backend can actually resolve, and
    // marks them non-editable; there is no draft state to render.
    { id: "status", header: "状态", render: () => <span className="ref-chip tone-ok">启用</span> },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>策略与预算</h1>
        <p>任务模式在创建时冻结的执行策略预设（只读）</p>
      </div>
    </header>

    <DetailTabs tabs={TABS} active={tab} onSelect={setTab} size="lg" />

    {tab !== "execution" ? <EmptyState label={`暂无${TABS.find((item) => item.id === tab)?.label}数据`} />
      : query.isLoading ? <LoadingSkeleton label="正在读取执行策略预设" rows={5} />
      : query.isError ? <ErrorState
        description={query.error instanceof Error ? query.error.message : "无法读取执行策略目录"}
        actionLabel="重试"
        onAction={() => void query.refetch()}
      />
      : <div className="ref-master-detail policies-layout ref-fill">
        <section className="ref-card">
          <header className="ref-card-head"><h2>执行策略预设</h2></header>
          <label className="ref-search">
            <Search size={16} aria-hidden="true" />
            <input
              aria-label="搜索策略名称"
              placeholder="搜索策略名称、模式或预设..."
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <CatalogTable
            columns={columns}
            rows={rows}
            rowKey={(row) => row.id}
            selectedKey={selected?.id}
            onSelect={(row) => setSelectedId(row.id)}
            emptyLabel="没有匹配的执行策略"
          />
        </section>

        {selected ? <PolicyDetail record={selected} /> : null}
      </div>}
  </div>;
}

function PolicyDetail({ record }: { record: ExecutionPolicyRecord }) {
  const { network, local_compute: compute, high_impact: impact } = record.execution_policy;
  return <section className="ref-detail-panel" aria-label={`${record.id} 详情`}>
    <header className="ref-detail-head">
      <div className="ref-detail-title">
        <h2>{record.id}</h2>
        <span className="ref-chip tone-info">{PRESET_LABELS[record.preset] ?? record.preset}</span>
      </div>
      <span className="ref-chip tone-ok">启用</span>
    </header>
    <p className="skill-summary">{record.mode_label} · 来源：{record.source}</p>

    <h3 className="ref-subhead">网络访问</h3>
    <FieldGrid columns={2} fields={[
      { label: "访问范围", value: NETWORK_ACCESS_LABELS[network.access] ?? network.access },
      { label: "交互方式", value: INTERACTION_LABELS[network.interaction] ?? network.interaction },
      { label: "速率限制", value: `${network.rate_limit_per_minute} 次/分钟` },
      { label: "并发限制", value: network.concurrency },
      { label: "请求超时", value: `${network.request_timeout_seconds} 秒` },
      { label: "拒绝私有网段", value: network.deny_private_networks ? "是" : "否" },
      { label: "拒绝回环地址", value: network.deny_loopback ? "是" : "否" },
      { label: "拒绝链路本地", value: network.deny_link_local ? "是" : "否" },
      { label: "拒绝云元数据", value: network.deny_cloud_metadata ? "是" : "否" },
      { label: "种子来源", value: network.seed_origins.join("、"), missing: !network.seed_origins.length },
    ]} />

    <h3 className="ref-subhead">本地计算</h3>
    <FieldGrid columns={2} fields={[
      { label: "执行模式", value: COMPUTE_LABELS[compute.mode] ?? compute.mode },
      { label: "超时", value: `${compute.timeout_seconds} 秒` },
      { label: "并发限制", value: compute.concurrency },
      { label: "网络继承", value: compute.network_inheritance },
    ]} />

    <h3 className="ref-subhead">高影响操作</h3>
    <FieldGrid columns={2} fields={[
      { label: "处理方式", value: HIGH_IMPACT_LABELS[impact.mode] ?? impact.mode },
      { label: "放行操作", value: impact.allowed_actions.join("、"), missing: !impact.allowed_actions.length },
    ]} />

    <ul className="policy-notes">
      <li><Info size={13} aria-hidden="true" />创建任务时生成不可变快照</li>
      <li><Info size={13} aria-hidden="true" />后端未提供策略编辑接口，此页为只读</li>
    </ul>
  </section>;
}
