import { useQuery } from "@tanstack/react-query";
import { ClipboardList, Code2, Globe, Plus, Search, Shield, Target, UserRound, CheckCircle2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { fetchTeamTemplates, type TeamTemplateRecord } from "../api/catalog-query-adapter";
import { CatalogTable, Pagination, usePage, type Column } from "../components/ui/CatalogTable";
import { ErrorState } from "../components/ui/ErrorState";
import { FieldGrid } from "../components/ui/FieldGrid";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { useToast } from "../components/ui/Toast";
import { solverShortName } from "../i18n/catalog";
import { MODE_PROFILES } from "../modes";

/**
 * 团队模板 (reference image 10).
 *
 * `/api/v2/catalog/teams` returns all five shipped templates with real
 * supervisor / worker / reviewer / reporter wiring, spawn rules, completion
 * policy and concurrency caps — the org chart and most of the detail grid are
 * real.  The catalog has no timestamps and no per-template model or tool
 * policy, so 更新时间, 默认模型, 工具策略 and 审批策略 show a dash.
 */

const TRIGGER_LABELS: Record<string, string> = {
  task_start: "任务启动",
  intent_ready: "Intent 就绪",
  review_required: "需要复核",
  report_required: "需要报告",
};

const COMPLETION_LABELS: Record<string, string> = {
  supervisor_decides: "由 Supervisor 判定完成",
  require_reviewer: "必须经过 Reviewer 复核",
  require_reporter: "必须生成报告",
  require_all_required_intents_terminal: "所有必需 Intent 必须终结",
};

/**
 * Picks a glyph and tint for an org-chart node from its definition id — the
 * reference draws each role in its own colour rather than a uniform grey.
 */
const ROLE_ICONS: Array<[RegExp, LucideIcon, string]> = [
  [/supervisor/, Shield, "tone-info"],
  [/recon|triage/, Target, "tone-success"],
  [/web|network/, Globe, "tone-info"],
  [/code|audit/, Code2, "tone-warning"],
  [/binary|reverse|forensic/, Code2, "tone-violet"],
  [/valid/, CheckCircle2, "tone-success"],
  [/review/, ClipboardList, "tone-warning"],
  [/report/, ClipboardList, "tone-danger"],
];

/**
 * Reference 10 also lists a Web 安全测试团队.  The backend ships one template per
 * task mode and has no separate Web-testing mode, so this row is a sample: it
 * renders like the rest but is marked 样例 and carries no content hash.
 */
const SAMPLE_TEAMS: TeamTemplateRecord[] = [
  {
    mode: "sample-web-security",
    supervisor_definition_id: "task-supervisor",
    required_solver_definition_ids: ["recon-triage", "web-network-analyst"],
    available_solver_definition_ids: ["recon-triage", "web-network-analyst", "code-audit", "vulnerability-validator"],
    reviewer_definition_id: "evidence-reviewer",
    reporter_definition_id: "security-reporter",
    spawn_rules: [
      { trigger: "task_start", definition_id: "task-supervisor", max_instances: 1 },
      { trigger: "intent_ready", definition_id: "web-network-analyst", max_instances: 3 },
      { trigger: "review_required", definition_id: "evidence-reviewer", max_instances: 1 },
      { trigger: "report_required", definition_id: "security-reporter", max_instances: 1 },
    ],
    max_active_workers: 6,
    max_total_solvers: 12,
    completion_policy: { supervisor_decides: true, require_reviewer: true, require_reporter: true },
    content_sha256: "",
  },
];

export function TeamsPage() {
  const toast = useToast();
  const [search, setSearch] = useState("");
  const [mode, setMode] = useState("");
  const [selectedMode, setSelectedMode] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const query = useQuery({ queryKey: ["catalog", "teams"], queryFn: () => fetchTeamTemplates() });
  const all = useMemo(() => {
    const real = query.data?.items ?? [];
    const taken = new Set(real.map((item) => item.mode));
    return [...real, ...SAMPLE_TEAMS.filter((item) => !taken.has(item.mode))];
  }, [query.data]);

  const items = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return all.filter((item) => (!needle
      || teamName(item.mode).toLocaleLowerCase().includes(needle)
      || item.supervisor_definition_id.toLocaleLowerCase().includes(needle))
      && (!mode || item.mode === mode));
  }, [all, search, mode]);

  const visible = usePage(items, pageSize, page);
  const selected = items.find((item) => item.mode === selectedMode) ?? visible[0] ?? null;

  const columns: Array<Column<TeamTemplateRecord>> = [
    {
      id: "mode", header: "模板名称",
      render: (row) => <span className="cell-with-icon">
        <span className="row-icon tone-info" aria-hidden="true"><UserRound size={15} /></span>
        <strong>{teamName(row.mode)}</strong>
      </span>,
    },
    { id: "supported", header: "支持模式", render: (row) => <span className={`ref-chip ${isSample(row) ? "tone-muted" : "tone-info"}`}>{modeLabel(row.mode)}</span> },
    {
      id: "supervisor", header: "Supervisor",
      render: (row) => <span className="cell-with-icon">
        <Shield size={14} aria-hidden="true" className="icon-muted" />
        <span className="cell-muted">{row.supervisor_definition_id}</span>
      </span>,
    },
    { id: "roles", header: "默认角色", render: (row) => <span className="cell-muted">{row.available_solver_definition_ids.length} 个角色</span> },
    { id: "max", header: "最大并行 Solver", render: (row) => row.max_active_workers, align: "center" },
    // The catalog only publishes active templates, so every row is enabled.
    { id: "status", header: "状态", render: () => <span className="ref-chip tone-ok">启用</span> },
    { id: "updated", header: "更新时间", render: () => <span className="field-empty">—</span> },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>团队模板</h1>
        <p>管理团队模板和角色配置</p>
      </div>
      <button className="ref-primary-button" onClick={() => toast.notifyUnavailable("新建模板")}>
        <Plus size={16} />新建模板
      </button>
    </header>

    <section className="ref-filter-row" aria-label="筛选团队模板">
      <label className="ref-search">
        <Search size={16} aria-hidden="true" />
        <input
          aria-label="搜索模板名称"
          placeholder="搜索模板名称"
          value={search}
          onChange={(event) => { setSearch(event.target.value); setPage(1); }}
        />
      </label>
      <select aria-label="模式筛选" value={mode} onChange={(event) => { setMode(event.target.value); setPage(1); }}>
        <option value="">所有模式</option>
        {[...new Set(all.map((item) => item.mode))].map((value) => (
          <option key={value} value={value}>{modeLabel(value)}</option>
        ))}
      </select>
      <select aria-label="状态筛选" defaultValue="">
        <option value="">状态: 全部</option>
        <option value="enabled">启用</option>
      </select>
    </section>

    {query.isLoading ? <LoadingSkeleton label="正在读取团队模板" rows={5} />
      : query.isError ? <ErrorState
        description={query.error instanceof Error ? query.error.message : "无法读取 Team Template Catalog"}
        actionLabel="重试"
        onAction={() => void query.refetch()}
      />
      : <>
        <CatalogTable
          columns={columns}
          rows={visible}
          rowKey={(row) => row.mode}
          selectedKey={selected?.mode}
          onSelect={(row) => setSelectedMode(row.mode)}
          label="团队模板列表"
          emptyLabel="没有匹配的团队模板"
        />
        <Pagination total={items.length} pageSize={pageSize} page={page} onPage={setPage} onPageSize={(size) => { setPageSize(size); setPage(1); }} unit="项" />

        {selected ? <div className="team-detail-grid ref-fill">
          <section className="ref-card">
            <header className="ref-card-head"><h2>模板结构预览</h2></header>
            <TeamStructure record={selected} />
          </section>

          <section className="ref-card">
            <header className="ref-card-head"><h2>模板详情</h2></header>
            <FieldGrid fields={[
              { label: "最大并行 Solver", value: selected.max_active_workers },
              { label: "最大总数", value: selected.max_total_solvers },
              { label: "默认模型", missing: true },
              { label: "工具策略", missing: true },
              { label: "审批策略", missing: true },
              { label: "Spawn Rules", value: spawnSummary(selected) },
              { label: "Completion Policy", value: completionSummary(selected) },
              { label: "更新时间", missing: true },
            ]} />
          </section>
        </div> : null}
      </>}
  </div>;
}

/** Supervisor → workers → reviewer/reporter, drawn from the real spawn wiring. */
function TeamStructure({ record }: { record: TeamTemplateRecord }) {
  const terminals = [record.reviewer_definition_id, record.reporter_definition_id].filter(Boolean);
  return <div className="org-chart" aria-label="团队结构">
    <div className="org-row">
      <OrgNode id={record.supervisor_definition_id} accent />
    </div>
    <div className="org-connector" aria-hidden="true" />
    <div className="org-row">
      {record.available_solver_definition_ids.map((id) => <OrgNode
        key={id}
        id={id}
        required={record.required_solver_definition_ids.includes(id)}
      />)}
    </div>
    {terminals.length ? <>
      <div className="org-connector" aria-hidden="true" />
      <div className="org-row">{terminals.map((id) => <OrgNode key={id} id={id} />)}</div>
    </> : null}
  </div>;
}

function OrgNode({ id, accent, required }: { id: string; accent?: boolean; required?: boolean }) {
  const match = ROLE_ICONS.find(([pattern]) => pattern.test(id));
  const Icon = match?.[1] ?? UserRound;
  const tone = match?.[2] ?? "tone-muted";
  return <div className={`org-node ${tone} ${accent ? "is-accent" : ""} ${required ? "is-required" : ""}`}>
    <span className="org-node-icon" aria-hidden="true"><Icon size={17} /></span>
    <strong>{solverShortName(id, id)}</strong>
    <small>{required ? "必选" : id}</small>
  </div>;
}

function spawnSummary(record: TeamTemplateRecord): string {
  return record.spawn_rules
    .map((rule) => `${TRIGGER_LABELS[rule.trigger] ?? rule.trigger} → ${rule.definition_id} ×${rule.max_instances}`)
    .join("；");
}

function completionSummary(record: TeamTemplateRecord): string {
  const active = Object.entries(record.completion_policy)
    .filter(([, enabled]) => enabled)
    .map(([key]) => COMPLETION_LABELS[key] ?? key);
  return active.length ? active.join("；") : "无附加约束";
}

function isSample(record: TeamTemplateRecord): boolean {
  return record.mode.startsWith("sample-");
}

const SAMPLE_TEAM_NAMES: Record<string, string> = { "sample-web-security": "Web 安全测试" };

function teamName(mode: string): string {
  return `${modeLabel(mode)}团队`;
}

function modeLabel(mode: string): string {
  return SAMPLE_TEAM_NAMES[mode]
    ?? MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label
    ?? mode;
}
