import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Search, Upload } from "lucide-react";
import { deleteSkill, fetchSkillCorpus, fetchSkillDetail, fetchSkillSettings, importSkill, updateSkill, type SkillDetail, type SkillPublication, type SkillSetting } from "../api/tasks";
import { CapabilityNotice, Chip, DefinitionList, ProductEmpty, ProductPageHeader, ProductTable, ProductTabs } from "../components/ui/ProductPrimitives";
import { MODE_PROFILES, TASK_MODES, type TaskMode } from "../modes";

type SkillDraft = Pick<SkillDetail, "modes" | "capabilities" | "tags" | "version" | "body">;
type SkillCategory = "全部技能" | "通用技能" | "信息收集" | "漏洞分析" | "利用与验证" | "取证与响应" | "报告与总结" | "逆向工程";
const CATEGORIES: SkillCategory[] = ["全部技能", "通用技能", "信息收集", "漏洞分析", "利用与验证", "取证与响应", "报告与总结", "逆向工程"];
const DETAIL_TABS = ["概览", "Instructions", "参数模式", "依赖关系", "使用统计", "版本历史"];

export function SkillsPage() {
  const [skills, setSkills] = useState<SkillSetting[]>([]);
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const [selectedName, setSelectedName] = useState("");
  const [category, setCategory] = useState<SkillCategory>("全部技能");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("");
  const [tab, setTab] = useState("概览");
  const [draft, setDraft] = useState<SkillDraft | null>(null);
  const [publications, setPublications] = useState<SkillPublication[]>([]);
  const [error, setError] = useState(""); const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    setLoading(true);
    try {
      const corpusRequest = typeof fetchSkillCorpus === "function"
        ? fetchSkillCorpus()
        : Promise.resolve({ schema_version: 1, publications: [] as SkillPublication[] });
      const [value, corpus] = await Promise.all([fetchSkillSettings(), corpusRequest]);
      setSkills(value.skills); setPublications(corpus.publications); setError("");
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取 Skills"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const rows = useMemo(() => skills.filter((skill) => {
    const itemCategory = categoryFor(skill);
    const matchesCategory = category === "全部技能" || itemCategory === category;
    const matchesMode = !mode || skill.modes.includes(mode as TaskMode);
    const needle = query.trim().toLocaleLowerCase();
    const matchesQuery = !needle || `${skill.name} ${skill.summary} ${skill.tags.join(" ")}`.toLocaleLowerCase().includes(needle);
    return matchesCategory && matchesMode && matchesQuery;
  }), [category, mode, query, skills]);

  useEffect(() => {
    if (!selectedName && rows[0]) void openSkill(rows[0]);
  }, [rows, selectedName]);

  const openSkill = async (skill: SkillSetting) => {
    setSelectedName(skill.name); setBusy(true); setError(""); setDraft(null); setTab("概览");
    try { setSelected((await fetchSkillDetail(skill.name)).skill); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取 Skill 详情"); }
    finally { setBusy(false); }
  };

  const choose = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return;
    setBusy(true); setError(""); setMessage("");
    try { const result = await importSkill(file); setMessage(`${result.skill.name} 已导入。`); await load(); setSelectedName(result.skill.name); setSelected(result.skill); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Skill 导入失败"); }
    finally { setBusy(false); if (inputRef.current) inputRef.current.value = ""; }
  };

  const beginEdit = () => selected && setDraft({ modes: selected.modes, capabilities: selected.capabilities, tags: selected.tags, version: selected.version, body: selected.body });
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!selected || !draft) return; setBusy(true); setError("");
    try { const result = await updateSkill(selected.name, draft); setSelected(result.skill); setDraft(null); setMessage(`${selected.name} 已更新。`); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Skill 保存失败"); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!selected) return; setBusy(true); setError("");
    try { await deleteSkill(selected.name); setMessage(`${selected.name} 已删除或禁用。`); setSelected(null); setSelectedName(""); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Skill 删除失败"); }
    finally { setBusy(false); }
  };

  const publicationCounts = publications.reduce<Record<string, number>>((counts, item) => {
    counts[item.status] = (counts[item.status] ?? 0) + 1;
    return counts;
  }, {});

  return <section className="product-page skills-catalog-page">
    <section className="skill-message" aria-label="RAG Skill publication status">
      <b>RAG Skill Corpus</b>
      <span>{["published", "reviewed", "draft", "deprecated", "revoked"].map((status) => `${status}: ${publicationCounts[status] ?? 0}`).join(" | ")}</span>
      <small>Published means eligible for governed selection. Active is recorded only after Solver validation and snapshot freezing.</small>
    </section>
    <ProductPageHeader title="Skills" description="按分类浏览方法能力，查看 Instructions、适用 Mode 和所需 Capabilities。" action={<><input ref={inputRef} hidden type="file" accept=".md,text/markdown" onChange={choose} /><button disabled={busy} onClick={() => inputRef.current?.click()}><Upload size={15} />导入 Skill</button></>} />
    {error ? <div className="inline-error" role="alert">{error}</div> : null}
    {message ? <div className="skill-message" role="status">{message}</div> : null}
    <div className="product-toolbar"><label className="toolbar-search"><Search size={15} /><input aria-label="搜索 Skill" placeholder="搜索名称、描述或标签" value={query} onChange={(event) => setQuery(event.target.value)} /></label><label><span>Category</span><select aria-label="Category 筛选" value={category} onChange={(event) => setCategory(event.target.value as SkillCategory)}>{CATEGORIES.map((item) => <option key={item}>{item}</option>)}</select></label><label><span>Status</span><select aria-label="Status 筛选"><option>全部状态</option><option>Enabled</option></select></label><label><span>Mode</span><select aria-label="Mode 筛选" value={mode} onChange={(event) => setMode(event.target.value)}><option value="">全部 Mode</option>{TASK_MODES.map((item) => <option key={item} value={item}>{MODE_PROFILES[item].label}</option>)}</select></label></div>
    <div className="skills-layout">
      <aside className="skill-categories" aria-label="Skill 分类">{CATEGORIES.map((item) => <button key={item} className={category === item ? "active" : ""} onClick={() => setCategory(item)}><span>{item}</span><b>{item === "全部技能" ? skills.length : skills.filter((skill) => categoryFor(skill) === item).length}</b></button>)}</aside>
      <div className="skills-main"><div className="skills-table-region">
        {loading ? <p className="skill-loading">正在读取 Skill 注册表...</p> : null}
        {!loading && !rows.length ? <ProductEmpty title="没有匹配的 Skill" description="尝试清除筛选，或导入一个真实 Markdown Skill。" /> : null}
        {rows.length ? <ProductTable label="Skill 表格" headers={["名称", "Category", "版本", "支持 Mode", "来源", "状态", "更新时间"]}>{rows.map((skill) => <tr key={skill.name} className={selectedName === skill.name ? "selected-row" : ""} onClick={() => void openSkill(skill)}><td><strong>{skill.name}</strong><small>{skill.summary}</small></td><td>{categoryFor(skill)}<small>前端分类</small></td><td>v{skill.version}</td><td>{skill.modes.map((item) => MODE_PROFILES[item].label).join("、")}</td><td><Chip tone={skill.source === "custom" ? "info" : "neutral"}>{skill.source === "custom" ? "用户" : "内置"}</Chip></td><td><Chip tone="success">Enabled</Chip></td><td>-</td></tr>)}</ProductTable> : null}
      </div>{selected ? <SkillDetailPanel skill={selected} tab={tab} setTab={setTab} draft={draft} setDraft={setDraft} busy={busy} onEdit={beginEdit} onSave={save} onDelete={remove} /> : <ProductEmpty title="选择一个 Skill" description="详情将在此区域显示，不使用弹窗。" />}</div>
    </div>
  </section>;
}

function SkillDetailPanel({ skill, tab, setTab, draft, setDraft, busy, onEdit, onSave, onDelete }: { skill: SkillDetail; tab: string; setTab: (value: string) => void; draft: SkillDraft | null; setDraft: (value: SkillDraft | null) => void; busy: boolean; onEdit: () => void; onSave: (event: FormEvent) => void; onDelete: () => void }) {
  return <article className="skill-detail-panel"><header><div><span className="detail-kicker">SKILL DETAIL</span><h2>{skill.name}</h2><p>{skill.summary}</p></div><div><button className="secondary-button" disabled={busy} onClick={onEdit}>修改</button><button className="danger-button" disabled={busy} onClick={onDelete}>{skill.source === "builtin" ? "禁用" : "删除"}</button></div></header>
    <DefinitionList rows={[["版本", `v${skill.version}`], ["来源", skill.source === "custom" ? "用户导入" : "内置"], ["Category", `${categoryFor(skill)}（前端分类）`], ["Mode", skill.modes.map((item) => MODE_PROFILES[item].label).join("、")], ["Required Capabilities", skill.capabilities.join("、") || "无"], ["Tags", skill.tags.join("、") || "无"], ["使用情况", "后端未提供统计"], ["更新时间", "后端未提供"]]} />
    <ProductTabs items={DETAIL_TABS} active={tab} onChange={setTab} label="Skill 详情" />
    {draft ? <SkillEditor draft={draft} setDraft={setDraft} busy={busy} onSubmit={onSave} onCancel={() => setDraft(null)} /> : <SkillTab tab={tab} skill={skill} />}
  </article>;
}

function SkillTab({ tab, skill }: { tab: string; skill: SkillDetail }) {
  if (tab === "概览") return <section className="skill-overview"><h3>描述</h3><p>{skill.summary}</p><h3>适用条件</h3><div>{skill.tags.map((tag) => <Chip key={tag}>#{tag}</Chip>)}</div></section>;
  if (tab === "Instructions") return <section className="skill-instructions"><pre>{skill.body}</pre></section>;
  return <section className="skill-unsupported"><CapabilityNotice state="unsupported" reason={`后端尚未提供 Skill ${tab} 数据接口，未伪造生产数据。`} /><ProductEmpty title={`暂无${tab}`} description="该区域已按设计保留，接口可用后会展示真实数据。" /></section>;
}

function SkillEditor({ draft, setDraft, busy, onSubmit, onCancel }: { draft: SkillDraft; setDraft: (value: SkillDraft) => void; busy: boolean; onSubmit: (event: FormEvent) => void; onCancel: () => void }) {
  const toggleMode = (mode: TaskMode) => setDraft({ ...draft, modes: draft.modes.includes(mode) ? draft.modes.filter((item) => item !== mode) : [...draft.modes, mode] });
  return <form className="skill-editor inline" onSubmit={onSubmit}><fieldset><legend>适用 Mode</legend><div className="skill-mode-checks">{TASK_MODES.map((mode) => <label key={mode}><input type="checkbox" checked={draft.modes.includes(mode)} onChange={() => toggleMode(mode)} />{MODE_PROFILES[mode].label}</label>)}</div></fieldset><div className="skill-editor-fields"><label>版本<input aria-label="版本" value={draft.version} onChange={(event) => setDraft({ ...draft, version: event.target.value })} /></label><label>能力（逗号分隔）<input value={draft.capabilities.join(", ")} onChange={(event) => setDraft({ ...draft, capabilities: tokens(event.target.value) })} /></label><label>标签（逗号分隔）<input value={draft.tags.join(", ")} onChange={(event) => setDraft({ ...draft, tags: tokens(event.target.value) })} /></label></div><label>Skill 正文<textarea aria-label="Skill 正文" value={draft.body} onChange={(event) => setDraft({ ...draft, body: event.target.value })} /></label><footer><button type="button" className="secondary-button" disabled={busy} onClick={onCancel}>取消</button><button disabled={busy || !draft.modes.length || !draft.body.trim()}>{busy ? "保存中..." : "保存修改"}</button></footer></form>;
}

function categoryFor(skill: SkillSetting): SkillCategory {
  const tags = skill.tags.map((item) => item.toLocaleLowerCase());
  const text = `${skill.name} ${tags.join(" ")}`.toLocaleLowerCase();
  if (/reverse|binary|malware|firmware|逆向/.test(text)) return "逆向工程";
  if (/report|summary|writeup|报告/.test(text)) return "报告与总结";
  if (/forensic|incident|response|取证|响应/.test(text)) return "取证与响应";
  if (/exploit|poc|verify|利用|验证/.test(text)) return "利用与验证";
  if (/vuln|audit|analysis|漏洞/.test(text)) return "漏洞分析";
  if (/recon|collect|search|scan|信息|发现/.test(text)) return "信息收集";
  return "通用技能";
}

function tokens(value: string): string[] { return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean); }
