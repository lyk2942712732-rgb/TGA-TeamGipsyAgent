import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutGrid, Plus, RefreshCw, Rows3, Save, Search, Shield, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  checkSolverKaliHealth,
  fetchHostCapabilities,
  fetchHostCapabilityProfiles,
  fetchKaliProfiles,
  fetchSolverDefinitions,
  fetchSolverKaliHealth,
  fetchSolverKaliHealthSummary,
  fetchSolverManifest,
  updateSolverCapabilities,
  type KaliHealthStatus,
  type SolverDefinitionRecord,
  type SolverKaliHealth,
} from "../api/catalog-query-adapter";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { ChipList, FieldGrid } from "../components/ui/FieldGrid";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { RiskBadge } from "../components/ui/RiskBadge";
import { useToast } from "../components/ui/Toast";
import { MODE_PROFILES } from "../modes";

const ROLE_LABELS: Record<string, string> = {
  supervisor: "Supervisor",
  worker: "Worker",
  reviewer: "Reviewer",
  reporter: "Reporter",
};

const TABS: DetailTab[] = [
  { id: "basic", label: "基础配置" },
  { id: "instructions", label: "Instructions 模板" },
  { id: "tools", label: "能力（Tools）" },
  { id: "skills", label: "默认 Skills" },
  { id: "contract", label: "输出合约" },
  { id: "versions", label: "版本" },
  { id: "kali", label: "Kali 信息" },
];

type KaliCapability = "kali.exec" | "kali.session";
type Draft = {
  hostProfileId: string;
  hostAdd: string[];
  hostRemove: string[];
  profileId: string;
  capabilities: KaliCapability[];
};

export function SolversPage() {
  const toast = useToast();
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [mode, setMode] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState("basic");
  const [view, setView] = useState<"split" | "grid">("split");
  const query = useQuery({ queryKey: ["solvers"], queryFn: () => fetchSolverDefinitions() });
  const healthSummary = useQuery({ queryKey: ["solvers", "kali-health"], queryFn: fetchSolverKaliHealthSummary });
  const all = useMemo(() => query.data?.items ?? [], [query.data]);
  const healthBySolver = useMemo(
    () => new Map((healthSummary.data?.items ?? []).map((item) => [item.solver_id, item])),
    [healthSummary.data],
  );
  const roles = useMemo(() => [...new Set(all.map((item) => item.role))].sort(), [all]);
  const modes = useMemo(() => [...new Set(all.flatMap((item) => item.supported_modes))].sort(), [all]);
  const items = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return all.filter((item) => {
      const matchesText = !needle
        || item.id.toLocaleLowerCase().includes(needle)
        || item.specialties.some((value) => value.toLocaleLowerCase().includes(needle));
      return matchesText
        && (!role || item.role === role)
        && (!mode || item.supported_modes.includes(mode));
    });
  }, [all, search, role, mode]);
  const selected = items.find((item) => item.id === selectedId) ?? items[0] ?? null;

  const open = (id: string) => {
    setSelectedId(id);
    setTab("basic");
    setView("split");
  };

  return <div className="ref-page solvers-page">
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
          aria-label="搜索 Solver 名称或专长"
          placeholder="搜索 Solver 名称或专长..."
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
        <option value="">支持模式：全部</option>
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
      : <div className="ref-master-detail solver-management-layout ref-fill">
        <ul className="solver-list" aria-label="Solver 列表">
          {items.map((item) => <li key={item.id}>
            <button className={item.id === selected?.id ? "active" : ""} onClick={() => open(item.id)}>
              <span className="row-icon tone-info" aria-hidden="true"><Shield size={16} /></span>
              <span className="solver-list-copy">
                <strong>{item.id}</strong>
                <small>v{item.version}</small>
              </span>
              <SolverListStatus
                loading={healthSummary.isLoading}
                failed={healthSummary.isError}
                status={healthBySolver.get(item.id)?.status}
              />
            </button>
          </li>)}
        </ul>

        {selected ? <SolverDetail record={selected} tab={tab} onTab={setTab} /> : null}
      </div>}
  </div>;
}

function SolverListStatus({ loading, failed, status }: { loading: boolean; failed: boolean; status?: KaliHealthStatus }) {
  if (loading) return <span className="ref-chip tone-muted">检查中</span>;
  if (failed) return <span className="ref-chip tone-muted">状态未知</span>;
  if (!status || status === "host_only" || status === "healthy") return <span className="ref-chip tone-ok">启用</span>;
  return <KaliStatusBadge status={status} />;
}

function SolverDetail({ record, tab, onTab }: {
  record: SolverDefinitionRecord;
  tab: string;
  onTab: (id: string) => void;
}) {
  const client = useQueryClient();
  const profiles = useQuery({ queryKey: ["kali", "profiles"], queryFn: fetchKaliProfiles });
  const hostProfiles = useQuery({ queryKey: ["capabilities", "host-profiles"], queryFn: fetchHostCapabilityProfiles });
  const hostCapabilities = useQuery({ queryKey: ["capabilities", "host"], queryFn: fetchHostCapabilities });
  const manifest = useQuery({
    queryKey: ["solvers", record.id, "manifest", record.supported_modes[0]],
    queryFn: () => fetchSolverManifest(record.id, record.supported_modes[0]),
  });
  const kaliHealth = useQuery({
    queryKey: ["solvers", record.id, "kali-health"],
    queryFn: () => fetchSolverKaliHealth(record.id),
  });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(draftFrom(record));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setDraft(draftFrom(record));
    setEditing(false);
    setError("");
  }, [record]);

  const toggleKali = (capability: KaliCapability) => setDraft((value) => ({
    ...value,
    capabilities: value.capabilities.includes(capability)
      ? value.capabilities.filter((item) => item !== capability)
      : [...value.capabilities, capability],
  }));
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await updateSolverCapabilities(record.id, {
        expected_content_sha256: record.content_sha256,
        host_capability_profile_id: draft.hostProfileId,
        host_capability_overrides: { add: draft.hostAdd, remove: draft.hostRemove },
        kali: draft.capabilities.length ? { profile_id: draft.profileId, capabilities: draft.capabilities } : null,
      });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["solvers"] }),
        client.invalidateQueries({ queryKey: ["capabilities", "host"] }),
        client.invalidateQueries({ queryKey: ["capabilities", "kali"] }),
        client.invalidateQueries({ queryKey: ["kali", "profiles"] }),
        client.invalidateQueries({ queryKey: ["solvers", record.id, "manifest"] }),
        client.invalidateQueries({ queryKey: ["solvers", "kali-health"] }),
        client.invalidateQueries({ queryKey: ["solvers", record.id, "kali-health"] }),
      ]);
      setEditing(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };
  const selectTab = (nextTab: string) => {
    if (editing && nextTab !== "tools" && nextTab !== "kali") {
      setDraft(draftFrom(record));
      setEditing(false);
      setError("");
    }
    onTab(nextTab);
  };

  return <section className="ref-detail-panel solver-detail-panel" aria-label={`${record.id} 详情`}>
    <header className="ref-detail-head">
      <div className="ref-detail-title">
        <span className="row-icon tone-info" aria-hidden="true"><Shield size={17} /></span>
        <h2>{record.id}</h2>
        <span className="ref-version-chip">v{record.version}</span>
      </div>
      {editing ? <div className="policy-actions">
        <button className="ref-secondary-button" onClick={() => { setDraft(draftFrom(record)); setEditing(false); }}>
          <X size={14} />取消
        </button>
        <button
          className="ref-primary-button"
          disabled={busy || !draft.hostProfileId || (draft.capabilities.length > 0 && !draft.profileId)}
          onClick={() => void save()}
        >
          <Save size={14} />{busy ? "保存中" : "保存"}
        </button>
      </div> : <span className="ref-chip tone-ok"><i className="ref-dot" aria-hidden="true" />启用</span>}
    </header>

    {error ? <p className="inline-error" role="alert">{error}</p> : null}
    <FieldGrid fields={[
      { label: "角色", value: ROLE_LABELS[record.role] ?? record.role },
      { label: "专长", value: <ChipList values={record.specialties} /> },
      { label: "支持模式", value: <ChipList values={record.supported_modes.map(modeLabel)} tone="neutral" /> },
      { label: "Host Profile", value: record.host_capability_profile_id },
      { label: "默认 Skills", value: <ChipList values={record.required_skill_names} tone="neutral" /> },
      { label: "Manifest", value: manifest.isLoading ? "加载中" : manifest.isError ? "不可用" : "已验证" },
      { label: "描述", value: describe(record) },
    ]} />

    <DetailTabs tabs={TABS} active={tab} onSelect={selectTab} />

    <div className="ref-detail-body solver-tab-body">
      {tab === "basic" ? <BasicTab record={record} onInstructions={() => onTab("instructions")} /> : null}
      {tab === "instructions" ? <pre className="ref-prompt">{record.system_prompt_template}</pre> : null}
      {tab === "tools" ? <ToolsTab
        record={record}
        editing={editing}
        draft={draft}
        setDraft={setDraft}
        profiles={hostProfiles.data?.items ?? []}
        capabilities={hostCapabilities.data?.items ?? []}
        onEdit={() => setEditing(true)}
      /> : null}
      {tab === "skills" ? <FieldGrid fields={[
        { label: "默认 Skill 标签", value: <ChipList values={record.default_skill_tags} tone="neutral" /> },
        { label: "必需 Skills", value: <ChipList values={record.required_skill_names} /> },
      ]} /> : null}
      {tab === "contract" ? <FieldGrid fields={[
        { label: "合约名称", value: <code className="cell-mono">{record.output_contract.name}</code> },
        { label: "必填字段", value: <ChipList values={record.output_contract.required_fields} tone="neutral" /> },
        { label: "接受 Intent 类型", value: <ChipList values={record.accepted_intent_kinds} tone="neutral" /> },
        { label: "完成权限", value: record.completion_authority },
      ]} /> : null}
      {tab === "versions" ? <FieldGrid fields={[
        { label: "当前版本", value: `v${record.version}` },
        { label: "内容指纹", value: <code className="ref-hash">{record.content_sha256}</code> },
      ]} /> : null}
      {tab === "kali" ? editing
        ? <KaliEditor draft={draft} setDraft={setDraft} toggle={toggleKali} profiles={profiles.data?.items ?? []} />
        : <KaliHealthPanel
          record={record}
          health={kaliHealth.data}
          loading={kaliHealth.isLoading}
          failed={kaliHealth.isError}
          onRetry={() => void kaliHealth.refetch()}
        /> : null}
    </div>

    {!editing && (tab === "tools" || tab === "kali") ? <footer className="ref-detail-actions">
      <button className="ref-primary-button" onClick={() => setEditing(true)}>编辑能力</button>
    </footer> : null}
  </section>;
}

function BasicTab({ record, onInstructions }: { record: SolverDefinitionRecord; onInstructions: () => void }) {
  return <>
    <div className="solver-prompt-block">
      <h3>System Prompt 模板</h3>
      <div className="solver-prompt-box">
        <ol>{sentences(record.system_prompt_template).map((line, index) => <li key={index}>{line}</li>)}</ol>
        <button className="ref-link-button" onClick={onInstructions}>查看完整模板</button>
      </div>
    </div>
    <div className="solver-form-grid">
      <label>最大轮次<input value={record.default_budget.max_turns ?? "—"} readOnly aria-readonly="true" /></label>
      <label>最大输出 Tokens<input value={record.default_budget.max_output_tokens ?? "—"} readOnly aria-readonly="true" /></label>
      <label>截止时间<input value={record.default_budget.deadline ?? "—"} readOnly aria-readonly="true" /></label>
    </div>
    <div className="solver-form-grid cols-2">
      <label>输出格式<select value="JSON" disabled><option>JSON</option></select></label>
      <label>结构化输出<select value={record.output_contract.name} disabled><option>{record.output_contract.name}</option></select></label>
    </div>
    <FieldGrid columns={2} fields={budgetFields(record.default_budget)} />
  </>;
}

function ToolsTab({ record, editing, draft, setDraft, profiles, capabilities, onEdit }: {
  record: SolverDefinitionRecord;
  editing: boolean;
  draft: Draft;
  setDraft: (value: Draft) => void;
  profiles: Awaited<ReturnType<typeof fetchHostCapabilityProfiles>>["items"];
  capabilities: Awaited<ReturnType<typeof fetchHostCapabilities>>["items"];
  onEdit: () => void;
}) {
  const byCategory = Object.entries(record.host_capabilities.reduce<Record<string, typeof record.host_capabilities>>(
    (groups, item) => { (groups[item.category] ??= []).push(item); return groups; },
    {},
  ));
  if (editing) return <HostEditor draft={draft} setDraft={setDraft} role={record.role} profiles={profiles} capabilities={capabilities} />;
  return <section className="solver-capabilities">
    <header>
      <div><h3>Host 能力</h3><p>当前 Solver 可调用的 Host Tools，已按能力分类整理。</p></div>
      <button className="ref-secondary-button" onClick={onEdit}>编辑 Host 能力</button>
    </header>
    {!byCategory.length ? <EmptyState title="暂无 Host 能力" /> : byCategory.map(([category, values]) => <div className="solver-capability-group" key={category}>
      <h4>{category}</h4>
      <div className="capability-grid">
        {values.map((item) => <article className="capability-card" key={item.id}>
          <div className="capability-title">
            <div><h3>{item.id}</h3><p>{item.display_name}</p></div>
            <RiskBadge value={item.risk} />
          </div>
          <div className="capability-meta"><span>来源</span><small>{item.source}</small></div>
        </article>)}
      </div>
    </div>)}
  </section>;
}

function HostEditor({ draft, setDraft, role, profiles, capabilities }: {
  draft: Draft;
  setDraft: (value: Draft) => void;
  role: SolverDefinitionRecord["role"];
  profiles: Awaited<ReturnType<typeof fetchHostCapabilityProfiles>>["items"];
  capabilities: Awaited<ReturnType<typeof fetchHostCapabilities>>["items"];
}) {
  const base = new Set(profiles.find((item) => item.id === draft.hostProfileId)?.capability_ids ?? []);
  const selected = new Set([...base, ...draft.hostAdd].filter((id) => !draft.hostRemove.includes(id)));
  const available = capabilities.filter((item) => item.allowed_roles.includes(role));
  const toggle = (id: string) => {
    const enabled = selected.has(id);
    if (base.has(id)) {
      setDraft({
        ...draft,
        hostRemove: enabled ? [...draft.hostRemove, id] : draft.hostRemove.filter((item) => item !== id),
        hostAdd: draft.hostAdd.filter((item) => item !== id),
      });
    } else {
      setDraft({
        ...draft,
        hostAdd: enabled ? draft.hostAdd.filter((item) => item !== id) : [...draft.hostAdd, id],
        hostRemove: draft.hostRemove.filter((item) => item !== id),
      });
    }
  };
  return <div className="skill-editor">
    <label>Host Profile
      <select value={draft.hostProfileId} onChange={(event) => setDraft({ ...draft, hostProfileId: event.target.value, hostAdd: [], hostRemove: [] })}>
        {profiles.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
      </select>
    </label>
    <fieldset>
      <legend>能力（Tools）</legend>
      {available.map((item) => <label key={item.id}>
        <input type="checkbox" checked={selected.has(item.id)} onChange={() => toggle(item.id)} />
        {item.id}<small>{item.display_name}</small>
      </label>)}
    </fieldset>
  </div>;
}

function KaliEditor({ draft, setDraft, toggle, profiles }: {
  draft: Draft;
  setDraft: (value: Draft) => void;
  toggle: (value: KaliCapability) => void;
  profiles: Awaited<ReturnType<typeof fetchKaliProfiles>>["items"];
}) {
  const selected = profiles.find((item) => item.id === draft.profileId);
  return <div className="skill-editor">
    <label>Kali Profile
      <select value={draft.profileId} onChange={(event) => {
        const profile = profiles.find((item) => item.id === event.target.value);
        setDraft({
          ...draft,
          profileId: event.target.value,
          capabilities: draft.capabilities.filter((item) => profile?.supported_capabilities.includes(item)),
        });
      }}>
        <option value="">选择 Profile</option>
        {profiles.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.image}</option>)}
      </select>
    </label>
    <fieldset>
      <legend>Kali 能力</legend>
      {(["kali.exec", "kali.session"] as KaliCapability[]).map((capability) => <label key={capability}>
        <input
          type="checkbox"
          checked={draft.capabilities.includes(capability)}
          disabled={Boolean(selected && !selected.supported_capabilities.includes(capability))}
          onChange={() => toggle(capability)}
        />
        {capability}<small>{capability === "kali.exec" ? "一次性命令执行" : "交互式 PTY 会话"}</small>
      </label>)}
    </fieldset>
    <p className="skill-summary">取消全部能力会移除 Kali Binding，不会提交空能力列表。</p>
  </div>;
}

function KaliHealthPanel({ record, health, loading, failed, onRetry }: {
  record: SolverDefinitionRecord;
  health?: SolverKaliHealth;
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
}) {
  const [checking, setChecking] = useState(false);
  const [checkMessage, setCheckMessage] = useState("");
  const check = async () => {
    setChecking(true);
    setCheckMessage("");
    try {
      await checkSolverKaliHealth(record.id);
      onRetry();
    } catch (reason) {
      const status = typeof reason === "object" && reason && "status" in reason ? Number(reason.status) : 0;
      setCheckMessage(status === 501 ? "深度检查暂不可用" : "状态刷新失败");
    } finally {
      setChecking(false);
    }
  };
  if (loading) return <div className="kali-health-card"><KaliStatusBadge status="unknown" label="检查中" /><p>正在读取该 Solver 的 Kali 状态。</p></div>;
  if (failed || !health) return <div className="kali-health-card kali-health-error" role="alert">
    <KaliStatusBadge status="unknown" label="状态获取失败" />
    <p>无法读取 Kali 健康状态，未将其视为健康。</p>
    <button className="ref-secondary-button" onClick={onRetry}>重试</button>
  </div>;
  if (!health.requires_kali) return <div className="kali-health-card">
    <header><div><span>Kali 信息</span><h4>Host only</h4></div><KaliStatusBadge status="host_only" /></header>
    <p>此 Solver 不需要 Kali 运行环境。</p>
  </div>;
  return <div className="kali-health-card">
    <header><div><span>Kali 信息</span><h4>{health.profile_id}</h4></div><KaliStatusBadge status={health.status} /></header>
    <div className="kali-health-grid">
      <div><span>Profile</span><strong>{health.profile_id}</strong></div>
      <div><span>Overall</span><KaliStatusBadge status={health.status} /></div>
      <div className="kali-health-wide"><span>Image</span><code>{health.image ?? "未配置"}</code></div>
      <div><span>Image status</span><KaliStatusBadge status={normalizeHealthStatus(health.image_status)} label={healthLabel(health.image_status)} /></div>
      <div><span>Runtime status</span><strong>{runtimeLabel(health.runtime_status)}</strong></div>
    </div>
    {health.reasons.length ? <div className="kali-health-reasons">
      <span>Details</span>
      <ul>{health.reasons.map((reason) => <li key={`${reason.code}:${reason.message}`}><strong>{reason.code}</strong>{reason.message}</li>)}</ul>
    </div> : null}
    {health.missing_executables.length ? <p className="kali-missing-tools">缺少工具：{health.missing_executables.join("、")}</p> : null}
    <footer>
      <small>{health.checked_at ? `检查时间：${new Date(health.checked_at).toLocaleString()}` : "尚未完成检查"}</small>
      <button className="ref-secondary-button" disabled={checking} onClick={() => void check()}>
        <RefreshCw size={14} />{checking ? "检查中" : "重新检查"}
      </button>
    </footer>
    {checkMessage ? <p className="inline-error" role="status">{checkMessage}</p> : null}
  </div>;
}

function KaliStatusBadge({ status, label }: { status: KaliHealthStatus; label?: string }) {
  const meta = HEALTH_META[status] ?? HEALTH_META.unknown;
  return <span className={`status-badge-v2 tone-${meta.tone} status-${status}`} title={meta.description}>
    <i aria-hidden="true" />{label ?? meta.label}
  </span>;
}

const HEALTH_META: Record<KaliHealthStatus, {
  label: string;
  tone: "neutral" | "info" | "success" | "warning" | "danger";
  description: string;
}> = {
  host_only: { label: "Host only", tone: "info", description: "此 Solver 不需要 Kali 运行环境。" },
  unknown: { label: "未知", tone: "warning", description: "尚未获得足够的健康信息。" },
  runtime_disabled: { label: "Runtime 已禁用", tone: "neutral", description: "Kali Runtime 当前已关闭。" },
  unresolved_digest: { label: "未发布", tone: "warning", description: "镜像尚未写入真实 Registry Digest。" },
  image_unreachable: { label: "Registry 不可达", tone: "danger", description: "无法访问镜像 Registry。" },
  image_not_found: { label: "镜像不存在", tone: "danger", description: "Registry 中不存在该镜像 digest。" },
  image_unverified: { label: "未验证", tone: "warning", description: "镜像尚未完成项目验证流程。" },
  toolset_mismatch: { label: "Toolset 不匹配", tone: "danger", description: "镜像 toolset digest 与 Profile 不一致。" },
  tools_missing: { label: "工具缺失", tone: "danger", description: "镜像缺少 Profile 要求的命令。" },
  runtime_unavailable: { label: "Runtime 不可用", tone: "danger", description: "底层 Runtime Provider 当前不可用。" },
  healthy: { label: "健康", tone: "success", description: "镜像和 Runtime 检查均通过。" },
};

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
    .map(([key, value]) => ({ label: BUDGET_LABELS[key] ?? key, value: (value as number).toLocaleString() }));
}

function describe(record: SolverDefinitionRecord): string {
  return sentences(record.system_prompt_template)[0] ?? record.specialties.join("、") ?? record.id;
}

function sentences(template: string): string[] {
  const lines = template
    .split(/(?:\r?\n)+|(?<=[.。!?！？])\s+/)
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.slice(0, 3);
}

function normalizeHealthStatus(status: string): KaliHealthStatus {
  return status in HEALTH_META ? status as KaliHealthStatus : "unknown";
}

function healthLabel(status: string): string {
  return HEALTH_META[normalizeHealthStatus(status)].label;
}

function runtimeLabel(status: string): string {
  return ({
    disabled: "已禁用",
    sandboxd_unavailable: "sandboxd 不可用",
    sandboxd_available: "sandboxd 可用",
    docker_sandbox_unavailable: "Docker Sandbox 不可用",
    docker_sandbox_available: "Docker Sandbox 可用",
    not_applicable: "不适用",
  } as Record<string, string>)[status] ?? status;
}

function modeLabel(mode: string): string {
  return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode;
}

function draftFrom(record: SolverDefinitionRecord): Draft {
  return {
    hostProfileId: record.host_capability_profile_id,
    hostAdd: [...record.host_capability_overrides.add],
    hostRemove: [...record.host_capability_overrides.remove],
    profileId: record.kali?.profile_id ?? "",
    capabilities: record.kali?.capabilities ?? [],
  };
}
