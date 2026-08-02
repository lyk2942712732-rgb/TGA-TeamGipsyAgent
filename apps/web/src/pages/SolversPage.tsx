import { useQuery } from "@tanstack/react-query";
import { LayoutGrid, Plus, Rows3, Search, Shield } from "lucide-react";
import { useMemo, useState } from "react";
import { fetchSolverDefinitions, type SolverDefinitionRecord } from "../api/catalog-query-adapter";
import { getLLMSettings } from "../api/tasks";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { ChipList, FieldGrid } from "../components/ui/FieldGrid";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { useToast } from "../components/ui/Toast";
import { termLabel } from "../i18n/catalog";
import { MODE_PROFILES } from "../modes";

/**
 * Solver 管理 (reference image 11).
 *
 * `/api/v2/catalog/solvers` returns nine real definitions with prompt template,
 * capabilities, tool groups, skill tags, output contract and default budget —
 * most of the page is real.  Gaps: there is no per-solver model (every solver
 * runs on the single configured provider, which is what 默认模型 shows), no
 * concurrency cap, no description field (描述 falls back to the first sentence
 * of the prompt template) and no version history.
 */

const ROLE_LABELS: Record<string, string> = {
  supervisor: "Supervisor", worker: "Worker", reviewer: "Reviewer", reporter: "Reporter",
};

const TABS: DetailTab[] = [
  { id: "basic", label: "基础配置" },
  { id: "instructions", label: "Instructions 模板" },
  { id: "tools", label: "能力 (Tools)" },
  { id: "skills", label: "默认 Skills" },
  { id: "contract", label: "输出合约" },
  { id: "versions", label: "版本", missing: true },
];

export function SolversPage() {
  const toast = useToast();
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [mode, setMode] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState("basic");
  const [view, setView] = useState<"split" | "grid">("split");

  const query = useQuery({ queryKey: ["catalog", "solvers"], queryFn: () => fetchSolverDefinitions() });
  // Shares the Models page key: every solver runs on the one configured provider.
  const llm = useQuery({ queryKey: ["llm-settings"], queryFn: getLLMSettings });
  const all = useMemo(() => query.data?.items ?? [], [query.data]);

  const items = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return all.filter((item) => {
      const matchesText = !needle
        || item.id.toLocaleLowerCase().includes(needle)
        || item.specialties.some((value) => value.toLocaleLowerCase().includes(needle));
      return matchesText
        && (!role || item.orchestration_role === role)
        && (!mode || item.supported_modes.includes(mode));
    });
  }, [all, search, role, mode]);

  const selected = items.find((item) => item.id === selectedId) ?? items[0] ?? null;
  const roles = useMemo(() => [...new Set(all.map((item) => item.orchestration_role))].sort(), [all]);
  const modes = useMemo(() => [...new Set(all.flatMap((item) => item.supported_modes))].sort(), [all]);

  const open = (id: string) => { setSelectedId(id); setTab("basic"); setView("split"); };

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>Solver 管理</h1>
        <p>管理 Solver 定义、版本和能力</p>
      </div>
      <button className="ref-primary-button" onClick={() => toast.notifyUnavailable("新建 Solver")}>
        <Plus size={16} />新建 Solver
      </button>
    </header>

    <section className="ref-filter-row" aria-label="筛选 Solver">
      <label className="ref-search">
        <Search size={16} aria-hidden="true" />
        <input
          aria-label="搜索 Solver 名称或角色"
          placeholder="搜索 Solver 名称或角色..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      <select aria-label="角色筛选" value={role} onChange={(event) => setRole(event.target.value)}>
        <option value="">所有角色</option>
        {roles.map((value) => <option key={value} value={value}>{ROLE_LABELS[value] ?? value}</option>)}
      </select>
      <select aria-label="状态筛选" defaultValue="">
        <option value="">所有状态</option>
        <option value="enabled">启用</option>
      </select>
      <select aria-label="支持模式筛选" value={mode} onChange={(event) => setMode(event.target.value)}>
        <option value="">支持模式: 全部</option>
        {modes.map((value) => <option key={value} value={value}>{modeLabel(value)}</option>)}
      </select>
      <div className="view-toggle push-end" role="group" aria-label="视图切换">
        <button className={view === "split" ? "active" : ""} aria-label="列表视图" aria-pressed={view === "split"} onClick={() => setView("split")}>
          <Rows3 size={16} />
        </button>
        <button className={view === "grid" ? "active" : ""} aria-label="网格视图" aria-pressed={view === "grid"} onClick={() => setView("grid")}>
          <LayoutGrid size={16} />
        </button>
      </div>
    </section>

    {query.isLoading ? <LoadingSkeleton label="正在读取 Solver 定义" rows={6} />
      : query.isError ? <ErrorState
        description={query.error instanceof Error ? query.error.message : "无法读取 Solver Definition Catalog"}
        actionLabel="重试"
        onAction={() => void query.refetch()}
      />
      : !items.length ? <EmptyState title="没有匹配的 Solver" description="调整搜索或筛选条件后重试。" />
      : view === "grid" ? <div className="solver-grid ref-fill">
        {items.map((item) => <button className="solver-card" key={item.id} onClick={() => open(item.id)}>
          <span className="row-icon tone-info" aria-hidden="true"><Shield size={16} /></span>
          <strong>{item.id}</strong>
          <small>v{item.version}</small>
          <span className="ref-chip tone-ok">启用</span>
          <p>{describe(item)}</p>
        </button>)}
      </div>
      : <div className="ref-master-detail ref-fill">
        <ul className="solver-list">
          {items.map((item) => <li key={item.id}>
            <button className={item.id === selected?.id ? "active" : ""} onClick={() => open(item.id)}>
              <span className="row-icon tone-info" aria-hidden="true"><Shield size={16} /></span>
              <span className="solver-list-copy">
                <strong>{item.id}</strong>
                <small>v{item.version}</small>
              </span>
              <span className="ref-chip tone-ok">启用</span>
            </button>
          </li>)}
        </ul>

        {selected ? <SolverDetail
          record={selected}
          model={llm.data?.configured ? llm.data.model : null}
          temperature={llm.data?.configured ? llm.data.temperature ?? null : null}
          tab={tab}
          onTab={setTab}
        /> : null}
      </div>}
  </div>;
}

function SolverDetail({ record, model, temperature, tab, onTab }: {
  record: SolverDefinitionRecord;
  model: string | null;
  temperature: number | null;
  tab: string;
  onTab: (id: string) => void;
}) {
  const toast = useToast();
  return <section className="ref-detail-panel" aria-label={`${record.id} 详情`}>
    <header className="ref-detail-head">
      <div className="ref-detail-title">
        <span className="row-icon tone-info" aria-hidden="true"><Shield size={17} /></span>
        <h2>{record.id}</h2>
        <span className="ref-version-chip">v{record.version}</span>
      </div>
      <span className="ref-chip tone-ok"><i className="ref-dot" aria-hidden="true" />启用</span>
    </header>

    <FieldGrid fields={[
      { label: "角色", value: ROLE_LABELS[record.orchestration_role] ?? record.orchestration_role },
      { label: "专长", value: <ChipList values={record.specialties.map(termLabel)} /> },
      { label: "支持模式", value: <ChipList values={record.supported_modes.map(modeLabel)} tone="neutral" /> },
      { label: "默认模型", value: model, missing: !model },
      { label: "最大并行", missing: true },
      { label: "描述", value: describe(record) },
    ]} />

    <DetailTabs tabs={TABS} active={tab} onSelect={onTab} />

    <div className="ref-detail-body">
      {tab === "basic" ? <>
        <div className="solver-prompt-block">
          <h3>System Prompt 模板</h3>
          <div className="solver-prompt-box">
            <ol>{sentences(record.system_prompt_template).map((line, index) => <li key={index}>{line}</li>)}</ol>
            {/* The English source template stays one click away, unedited. */}
            <button className="ref-link-button" onClick={() => onTab("instructions")}>查看完整模板</button>
          </div>
        </div>

        {/* Sampling settings live on the single configured provider, not on a
            Solver Definition; the deadline and token caps come from the
            definition's own default_budget. */}
        <FieldGrid columns={2} fields={[
          { label: "温度", value: temperature, missing: temperature === null },
          { label: "截止时间", value: record.default_budget.deadline, missing: !record.default_budget.deadline },
          { label: "结构化输出", value: <code className="cell-mono">{record.output_contract.name}</code> },
          ...budgetFields(record.default_budget),
        ]} />
      </> : null}

      {tab === "instructions" ? <pre className="ref-prompt">{record.system_prompt_template}</pre> : null}

      {tab === "tools" ? <FieldGrid fields={[
        { label: "必需能力", value: <ChipList values={record.required_capabilities} /> },
        { label: "允许的工具组", value: <ChipList values={record.allowed_tool_groups} tone="neutral" /> },
        { label: "工具策略", value: record.tool_policy_profile },
      ]} /> : null}

      {tab === "skills" ? <FieldGrid fields={[
        { label: "默认 Skill 标签", value: <ChipList values={record.default_skill_tags.map(termLabel)} tone="neutral" /> },
        { label: "必需 Skill", value: <ChipList values={record.required_skill_names} /> },
      ]} /> : null}

      {tab === "contract" ? <FieldGrid fields={[
        { label: "合约名称", value: <code className="cell-mono">{record.output_contract.name}</code> },
        { label: "必填字段", value: <ChipList values={record.output_contract.required_fields} tone="neutral" /> },
        { label: "接受 Intent 类型", value: <ChipList values={record.accepted_intent_kinds} tone="neutral" /> },
        { label: "完成权限", value: record.completion_authority },
        { label: "内容指纹", value: <code className="cell-mono">{record.content_sha256.slice(0, 16)}…</code> },
      ]} /> : null}

      {tab === "versions" ? <EmptyState label="暂无版本历史" /> : null}
    </div>

    <footer className="ref-detail-actions">
      <button className="ref-secondary-button" onClick={() => toast.notifyUnavailable("复制 Solver")}>复制</button>
      <button className="ref-primary-button" onClick={() => toast.notifyUnavailable("编辑 Solver")}>编辑</button>
    </footer>
  </section>;
}

const BUDGET_LABELS: Record<string, string> = {
  max_turns: "最大轮次",
  max_input_tokens: "最大输入 Token",
  max_output_tokens: "最大输出 Token",
  max_tool_calls: "最大工具调用",
  max_artifacts: "最大 Artifact 数",
  deadline: "截止时间",
};

function budgetFields(budget: SolverDefinitionRecord["default_budget"]) {
  return Object.entries(budget)
    .filter(([, value]) => typeof value === "number")
    .map(([key, value]) => ({
      label: BUDGET_LABELS[key] ?? key,
      value: (value as number).toLocaleString(),
    }));
}

/**
 * The catalog has no description field, so the definition's own prompt template
 * supplies the summary line rather than frontend-authored copy.
 */
function describe(record: SolverDefinitionRecord): string {
  return sentences(record.system_prompt_template)[0] ?? "";
}

function sentences(template: string): string[] {
  return template
    .split(/(?<=[.。!?！？])\s+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 3);
}

function modeLabel(mode: string): string {
  return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode;
}
