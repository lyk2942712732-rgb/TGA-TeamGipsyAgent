import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, Search, Trash2, Upload, X } from "lucide-react";
import { useRef, useMemo, useState, type ChangeEvent } from "react";
import { fetchSolverDefinitions } from "../api/catalog-query-adapter";
import {
  deleteSkill, fetchSkillDetail, fetchSkillSettings, importSkill, updateSkill,
  type SkillDetail, type SkillSetting,
} from "../api/tasks";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { CatalogTable, type Column } from "../components/ui/CatalogTable";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { ChipList, FieldGrid } from "../components/ui/FieldGrid";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { SKILL_CATEGORIES, skillCategory, skillLabel, skillSummary, termLabel } from "../i18n/catalog";
import { MODE_PROFILES, type TaskMode } from "../modes";

const TABS: DetailTab[] = [
  { id: "overview", label: "概览" },
  { id: "instructions", label: "Instructions" },
  { id: "params", label: "参数模式", missing: true },
  { id: "deps", label: "依赖关系" },
  { id: "usage", label: "使用统计", missing: true },
  { id: "history", label: "版本历史", missing: true },
];

export function SkillsPage() {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [tag, setTag] = useState("");
  const [mode, setMode] = useState("");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [tab, setTab] = useState("overview");
  const [message, setMessage] = useState("");
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const query = useQuery({ queryKey: ["settings", "skills"], queryFn: fetchSkillSettings });

  const onImport = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImporting(true);
    setMessage("");
    try {
      const result = await importSkill(file);
      await client.invalidateQueries({ queryKey: ["settings", "skills"] });
      setSelectedName(result.skill.name);
      setMessage(`已导入 Skill：${result.skill.name}`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "导入失败");
    } finally {
      setImporting(false);
    }
  };
  const all = useMemo(() => query.data?.skills ?? [], [query.data]);

  const items = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return all.filter((item) => (!needle
      || item.name.toLocaleLowerCase().includes(needle)
      || skillLabel(item.name).includes(needle)
      || item.tags.some((value) => value.toLocaleLowerCase().includes(needle)))
      && (!tag || skillCategory(item.tags) === tag)
      && (!mode || item.modes.includes(mode as TaskMode)));
  }, [all, search, tag, mode]);

  const selected = items.find((item) => item.name === selectedName) ?? items[0] ?? null;

  /**
   * Reference 12's rail is a fixed seven-category taxonomy, not the raw tag
   * list.  `SkillDocument` has no category field, so each Skill is filed by its
   * first tag that maps to one and the counts stay real.
   */
  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    for (const skill of all) {
      const category = skillCategory(skill.tags);
      counts.set(category, (counts.get(category) ?? 0) + 1);
    }
    return SKILL_CATEGORIES.map((value) => [value, counts.get(value) ?? 0] as const);
  }, [all]);

  const columns: Array<Column<SkillSetting>> = [
    {
      id: "name", header: "Skill 名称",
      // An imported Skill has no Chinese label, so its id is not printed twice.
      render: (row) => <span className="task-name-cell">
        <strong>{skillLabel(row.name)}</strong>
        {skillLabel(row.name) === row.name ? null : <small>{row.name}</small>}
      </span>,
    },
    { id: "category", header: "类别", render: (row) => <span className="cell-muted">{skillCategory(row.tags)}</span> },
    { id: "tags", header: "标签", render: (row) => <ChipList values={row.tags.slice(0, 3).map(termLabel)} tone="neutral" /> },
    { id: "version", header: "版本", render: (row) => `v${row.version}` },
    { id: "modes", header: "支持模式", render: (row) => <span className="cell-muted">{row.modes.map(modeLabel).join("、")}</span> },
    // Every skill the settings endpoint returns is loaded and selectable.
    { id: "status", header: "状态", render: () => <span className="ref-chip tone-ok">启用</span> },
    { id: "updated", header: "更新时间", render: () => <span className="field-empty">—</span> },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>Skills 管理</h1>
        <p>管理技能、版本和适用角色</p>
      </div>
    </header>

    {message ? <p className="settings-message" role="status">{message}</p> : null}

    <section className="ref-filter-row" aria-label="筛选 Skill">
      <label className="ref-search">
        <Search size={16} aria-hidden="true" />
        <input
          aria-label="搜索 Skill 名称或关键词"
          placeholder="搜索 Skill 名称或关键词..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      <select aria-label="类别筛选" value={tag} onChange={(event) => setTag(event.target.value)}>
        <option value="">所有类别</option>
        {categories.map(([value]) => <option key={value} value={value}>{value}</option>)}
      </select>
      <select aria-label="状态筛选" defaultValue="">
        <option value="">所有状态</option>
        <option value="enabled">启用</option>
      </select>
      <select aria-label="模式筛选" value={mode} onChange={(event) => setMode(event.target.value)}>
        <option value="">支持模式: 全部</option>
        {[...new Set(all.flatMap((item) => item.modes))].map((value) => (
          <option key={value} value={value}>{modeLabel(value)}</option>
        ))}
      </select>
      {/* The reference puts 新建 Skill here; the backend's real write path is
          Markdown import, so that is what the slot carries. */}
      <input
        ref={fileRef}
        type="file"
        accept=".md,text/markdown"
        hidden
        aria-label="选择 Skill Markdown 文件"
        onChange={(event) => void onImport(event)}
      />
      <button className="ref-primary-button push-end" disabled={importing} onClick={() => fileRef.current?.click()}>
        <Upload size={16} />{importing ? "导入中…" : "导入 Skill"}
      </button>
    </section>

    {query.isLoading ? <LoadingSkeleton label="正在读取 Skills" rows={6} />
      : query.isError ? <ErrorState
        description={query.error instanceof Error ? query.error.message : "无法读取 Skill 设置"}
        actionLabel="重试"
        onAction={() => void query.refetch()}
      />
      : <div className="skills-layout ref-fill">
        <aside className="skills-categories" aria-label="Skill 类别">
          <button className={tag === "" ? "active" : ""} onClick={() => setTag("")}>
            <span>全部技能</span><b>{all.length}</b>
          </button>
          {/* The taxonomy is fixed by the design, so every category stays listed;
              one with nothing filed under it is dimmed rather than hidden. */}
          {categories.map(([value, count]) => <button
            key={value}
            className={tag === value ? "active" : ""}
            disabled={count === 0}
            onClick={() => setTag(value)}
          ><span>{value}</span><b>{count}</b></button>)}
        </aside>

        <div className="skills-main">
          {!items.length
            ? <EmptyState title="没有匹配的 Skill" description="调整搜索或筛选条件后重试。" />
            : <CatalogTable
              fill
              columns={columns}
              rows={items}
              rowKey={(row) => row.name}
              selectedKey={selected?.name}
              onSelect={(row) => { setSelectedName(row.name); setTab("overview"); }}
            />}
        </div>
      </div>}

    {selected ? <SkillDetailPanel
      key={selected.name}
      skill={selected}
      tab={tab}
      onTab={setTab}
      onRemoved={() => setSelectedName(null)}
    /> : null}
  </div>;
}

/**
 * 使用情况 in the reference is "N 个 Solver".  There is no usage-statistics
 * endpoint, but the Solver catalog declares which skills each definition pulls
 * in, so the count is derived from the real wiring rather than sampled.
 */
function useSolverUsage(skill: SkillSetting): number | null {
  const solvers = useQuery({ queryKey: ["catalog", "solvers"], queryFn: () => fetchSolverDefinitions() });
  if (!solvers.data) return null;
  const tags = new Set(skill.tags);
  return solvers.data.items.filter((solver) => (
    solver.required_skill_names.includes(skill.name)
    || solver.default_skill_tags.some((value) => tags.has(value))
  )).length;
}

function splitList(value: string): string[] {
  return value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
}

function SkillDetailPanel({ skill, tab, onTab, onRemoved }: {
  skill: SkillSetting;
  tab: string;
  onTab: (id: string) => void;
  onRemoved: () => void;
}) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<SkillDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const usage = useSolverUsage(skill);

  const detail = useQuery({
    queryKey: ["settings", "skills", skill.name],
    queryFn: () => fetchSkillDetail(skill.name),
  });

  const startEdit = () => {
    if (detail.data) setDraft({ ...detail.data.skill });
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      await updateSkill(draft.name, {
        modes: draft.modes, capabilities: draft.capabilities,
        tags: draft.tags, version: draft.version, body: draft.body,
      });
      await client.invalidateQueries({ queryKey: ["settings", "skills"] });
      setDraft(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError("");
    try {
      await deleteSkill(skill.name);
      await client.invalidateQueries({ queryKey: ["settings", "skills"] });
      setConfirming(false);
      onRemoved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除失败");
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  };

  return <section className="ref-detail-panel" aria-label={`${skill.name} 详情`}>
    <header className="ref-detail-head">
      <div className="ref-detail-title">
        <h2>{skillLabel(skill.name)}</h2>
        <span className="ref-version-chip">v{skill.version}</span>
        <span className="ref-chip tone-info">{skillCategory(skill.tags)}</span>
        <span className="ref-chip tone-muted">{skill.source === "builtin" ? "内置" : "自定义"}</span>
        <span className="ref-chip tone-ok">启用</span>
      </div>
      <div className="policy-actions">
        {draft
          ? <>
            <button className="ref-secondary-button" disabled={busy} onClick={() => setDraft(null)}><X size={14} />取消</button>
            <button className="ref-primary-button" disabled={busy} onClick={() => void save()}><Save size={15} />{busy ? "保存中…" : "保存"}</button>
          </>
          : <>
            <button className="ref-secondary-button" disabled={!detail.data} onClick={startEdit}>编辑</button>
            {skill.editable && skill.source !== "builtin"
              ? <button className="ref-secondary-button" onClick={() => setConfirming(true)}><Trash2 size={14} />删除</button>
              : null}
          </>}
      </div>
    </header>

    {error ? <p className="inline-error" role="alert">{error}</p> : null}

    <ConfirmDialog
      open={confirming}
      title={`删除 Skill ${skillLabel(skill.name)}`}
      description="删除后该 Skill 不再参与任务选择。已创建任务的 Skill 快照不受影响。"
      confirmLabel="删除"
      danger
      busy={busy}
      onConfirm={() => void remove()}
      onCancel={() => setConfirming(false)}
    />

    <p className="skill-summary">{skillSummary(skill.name, skill.summary)}</p>

    <FieldGrid columns={2} fields={[
      { label: "适用模式", value: <ChipList values={skill.modes.map(modeLabel)} tone="neutral" /> },
      { label: "依赖能力", value: <ChipList values={skill.capabilities} /> },
      { label: "标签", value: <ChipList values={skill.tags.map(termLabel)} tone="neutral" /> },
      { label: "可编辑", value: skill.editable ? "是" : "否" },
      { label: "使用情况", value: usage === null ? null : `${usage} 个 Solver`, missing: usage === null },
      { label: "更新时间", missing: true },
    ]} />

    <DetailTabs tabs={TABS} active={tab} onSelect={onTab} />

    <div className="ref-detail-body">
      {tab === "overview" ? <p className="skill-summary">{skillSummary(skill.name, skill.summary)}</p> : null}
      {tab === "instructions" ? (
        draft
          ? <div className="skill-editor">
            <label>版本
              <input value={draft.version} onChange={(event) => setDraft({ ...draft, version: event.target.value })} />
            </label>
            <label>标签（逗号分隔）
              <input value={draft.tags.join(", ")}
                onChange={(event) => setDraft({ ...draft, tags: splitList(event.target.value) })} />
            </label>
            <label>依赖能力（逗号分隔）
              <input value={draft.capabilities.join(", ")}
                onChange={(event) => setDraft({ ...draft, capabilities: splitList(event.target.value) })} />
            </label>
            <label className="skill-editor-body">Instructions 正文
              <textarea rows={16} value={draft.body}
                onChange={(event) => setDraft({ ...draft, body: event.target.value })} />
            </label>
          </div>
          : detail.isLoading ? <LoadingSkeleton label="正在读取 Skill 正文" rows={4} />
            : detail.isError ? <ErrorState description="无法读取 Skill 正文" />
              : <pre className="ref-prompt">{detail.data?.skill.body}</pre>
      ) : null}
      {tab === "deps" ? <FieldGrid fields={[
        { label: "依赖能力", value: <ChipList values={skill.capabilities} /> },
        { label: "适用模式", value: <ChipList values={skill.modes.map(modeLabel)} tone="neutral" /> },
      ]} /> : null}
      {tab === "params" ? <EmptyState label="暂无参数模式数据" /> : null}
      {tab === "usage" ? <FieldGrid fields={[
        { label: "引用该 Skill 的 Solver", value: usage === null ? null : `${usage} 个`, missing: usage === null },
        { label: "调用次数", missing: true },
        { label: "最近使用", missing: true },
      ]} /> : null}
      {tab === "history" ? <EmptyState label="暂无版本历史" /> : null}
    </div>
  </section>;
}

function modeLabel(mode: string): string {
  return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode;
}
