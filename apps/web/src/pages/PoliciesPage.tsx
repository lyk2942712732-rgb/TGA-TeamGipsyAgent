import { useQuery } from "@tanstack/react-query";
import { Copy, Info, PencilLine, Plus, Search } from "lucide-react";
import { useState } from "react";
import { fetchProductCatalog } from "../api/catalog-query-adapter";
import { CatalogTable, type Column } from "../components/ui/CatalogTable";
import { EmptyState } from "../components/ui/EmptyState";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { useToast } from "../components/ui/Toast";

/**
 * Design skeleton with one real row.
 *
 * `/api/v2/catalog/policies` returns a single hard-coded placeholder record, so
 * the template list below is illustrative apart from that entry.
 */

type PolicyRow = {
  id: string;
  summary: string;
  modes: string;
  network: string;
  highImpact: string;
  status: "启用" | "草稿";
  sample: boolean;
  network_rules: string[];
  high_impact_rules: string[];
  budget_rules: string[];
};

const SAMPLE_POLICIES: PolicyRow[] = [
  {
    id: "safe-readonly", summary: "只读观察默认执行策略", modes: "全部", network: "关闭", highImpact: "禁止", status: "启用", sample: true,
    network_rules: ["完全禁止出网", "仅允许读取任务工作区"],
    high_impact_rules: ["禁止任何写入", "禁止不可逆操作"],
    budget_rules: ["最大运行 30 分钟", "Token 50k", "Tool Call 100"],
  },
  {
    id: "standard-pentest", summary: "渗透测试默认执行策略", modes: "渗透测试", network: "受限", highImpact: "需审批", status: "启用", sample: true,
    network_rules: ["仅允许任务 Scope", "禁止私网/回环/元数据地址", "30 请求/分钟 · 并发 2"],
    high_impact_rules: ["文件写入：每次审批", "资源修改/删除：每次审批", "不可逆动作必须给出替代方案"],
    budget_rules: ["最大运行 120 分钟", "Token 200k", "Tool Call 500 · Artifact 2 GB"],
  },
  {
    id: "isolated-code-analysis", summary: "隔离源码分析执行策略", modes: "逆向研究", network: "关闭", highImpact: "需审批", status: "启用", sample: true,
    network_rules: ["完全禁止出网", "仅允许读取本地源码"],
    high_impact_rules: ["写入限制在私有工作区", "禁止对外发起请求"],
    budget_rules: ["最大运行 90 分钟", "Token 150k", "Tool Call 300"],
  },
  {
    id: "malware-static-only", summary: "样本静态分析执行策略", modes: "逆向", network: "关闭", highImpact: "禁止", status: "启用", sample: true,
    network_rules: ["完全禁止出网", "禁止执行样本"],
    high_impact_rules: ["仅允许静态分析", "禁止动态执行"],
    budget_rules: ["最大运行 60 分钟", "Token 120k", "Artifact 1 GB"],
  },
  {
    id: "custom-lab", summary: "CTF 靶场自定义执行策略", modes: "CTF", network: "目标范围", highImpact: "预授权", status: "草稿", sample: true,
    network_rules: ["仅允许题目目标范围", "允许靶场回环地址"],
    high_impact_rules: ["靶场内写入预授权", "禁止跨题目横向访问"],
    budget_rules: ["最大运行 45 分钟", "Token 80k", "Tool Call 200"],
  },
];

const TABS: DetailTab[] = [
  { id: "execution", label: "执行策略" },
  { id: "tools", label: "工具策略", missing: true },
  { id: "budgets", label: "预算模板", missing: true },
  { id: "retention", label: "保留策略", missing: true },
];

export function PoliciesPage() {
  const toast = useToast();
  const [tab, setTab] = useState("execution");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(SAMPLE_POLICIES[1].id);

  const query = useQuery({ queryKey: ["catalog", "policies"], queryFn: () => fetchProductCatalog("policies") });

  const real: PolicyRow[] = (query.data?.items ?? []).map((item) => ({
    id: String(item.id ?? "task-execution-policy"),
    summary: "任务创建时冻结的执行策略契约",
    modes: "全部",
    network: "任务契约决定",
    highImpact: "任务契约决定",
    status: "启用",
    sample: false,
    network_rules: ["执行策略在创建任务时冻结为不可变快照"],
    high_impact_rules: ["高影响操作经 ToolGovernanceGateway 裁决"],
    budget_rules: ["预算由 Solver Definition 的 default_budget 提供"],
  }));

  const rows = [...real, ...SAMPLE_POLICIES].filter((row) => (
    !search.trim() || row.id.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())
  ));
  const selected = rows.find((row) => row.id === selectedId) ?? rows[0] ?? null;

  const columns: Array<Column<PolicyRow>> = [
    { id: "name", header: "名称", render: (row) => <strong className="policy-name">{row.id}</strong> },
    { id: "modes", header: "适用模式", render: (row) => <span className="cell-muted">{row.modes}</span> },
    { id: "network", header: "网络", render: (row) => <span className="cell-muted">{row.network}</span> },
    { id: "impact", header: "高影响", render: (row) => <span className={row.highImpact === "需审批" ? "policy-warn" : ""}>{row.highImpact}</span> },
    { id: "status", header: "状态", render: (row) => <span className={`ref-chip ${row.status === "启用" ? "tone-ok" : "tone-warn"}`}>{row.status}</span> },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>策略与预算</h1>
        <p>管理默认授权模板、工具策略与任务预算</p>
      </div>
      <button className="ref-primary-button" onClick={() => toast.notifyUnavailable("新建策略")}><Plus size={16} />新建策略</button>
    </header>

    <DetailTabs tabs={TABS} active={tab} onSelect={setTab} size="lg" />

    {tab !== "execution"
      ? <EmptyState label={`暂无${TABS.find((item) => item.id === tab)?.label}数据`} />
      : <div className="ref-master-detail policies-layout ref-fill">
        <section className="ref-card">
          <header className="ref-card-head"><h2>执行策略模板</h2></header>
          <label className="ref-search">
            <Search size={16} aria-hidden="true" />
            <input
              aria-label="搜索策略名称"
              placeholder="搜索策略名称、模式或标签..."
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
          />
        </section>

        {selected ? <section className="ref-detail-panel" aria-label={`${selected.id} 详情`}>
          <header className="ref-detail-head">
            <div className="ref-detail-title">
              <h2>{selected.id}</h2>
              <span className={`ref-chip ${selected.status === "启用" ? "tone-ok" : "tone-warn"}`}>{selected.status}</span>
            </div>
          </header>
          <p className="skill-summary">{selected.summary}</p>

          <RuleBlock title="网络访问" rules={selected.network_rules} />
          <RuleBlock title="高影响操作" rules={selected.high_impact_rules} />
          <RuleBlock title="预算上限" rules={selected.budget_rules} />

          <div className="policy-actions">
            <button className="ref-secondary-button" onClick={() => toast.notifyUnavailable("复制模板")}><Copy size={14} />复制模板</button>
            <button className="ref-primary-button" onClick={() => toast.notifyUnavailable("编辑新版本")}><PencilLine size={15} />编辑新版本</button>
          </div>

          <ul className="policy-notes">
            <li><Info size={13} aria-hidden="true" />创建任务时生成不可变快照</li>
            <li><Info size={13} aria-hidden="true" />Skill 与 Solver 不得扩大权限</li>
          </ul>
        </section> : null}
      </div>}
  </div>;
}

function RuleBlock({ title, rules }: { title: string; rules: string[] }) {
  return <section className="policy-rule-block">
    <h3>{title}</h3>
    <ul>{rules.map((rule) => <li key={rule}>{rule}</li>)}</ul>
  </section>;
}
