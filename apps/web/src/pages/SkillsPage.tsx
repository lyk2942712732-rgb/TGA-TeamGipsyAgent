import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePlus2, FileText, FolderPlus, HardDrive, PackageOpen, Save, Search, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import {
  createSkill, deleteSkill, deleteSkillDocument, fetchSkillDetail, fetchSkillDocument,
  fetchSkillSettings, importSkill, putSkillDocument, updateSkill,
  type SkillDetail, type SkillSetting,
} from "../api/tasks";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { ChipList, FieldGrid } from "../components/ui/FieldGrid";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

const byteLabel = (value: number) => value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`;
const tagsFrom = (value: string) => value.split(/[,，\n]/).map((item) => item.trim().toLowerCase()).filter(Boolean);

export function SkillsPage() {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState("");
  const [importing, setImporting] = useState(false);
  const zipRef = useRef<HTMLInputElement>(null);
  const query = useQuery({ queryKey: ["settings", "skills"], queryFn: fetchSkillSettings });
  const skills = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (query.data?.skills ?? []).filter((item) => !needle || `${item.name} ${item.summary} ${item.tags.join(" ")}`.toLowerCase().includes(needle));
  }, [query.data, search]);

  useEffect(() => {
    if (selectedName && !skills.some((item) => item.name === selectedName)) setSelectedName(skills[0]?.name ?? null);
    else if (!selectedName && skills.length) setSelectedName(skills[0].name);
  }, [skills, selectedName]);

  async function onImport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImporting(true); setMessage("");
    try {
      const result = await importSkill(file);
      await client.invalidateQueries({ queryKey: ["settings", "skills"] });
      setSelectedName(result.skill.name);
      setMessage(`已安装 Skill 包：${result.skill.name}`);
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "Skill ZIP 导入失败"); }
    finally { setImporting(false); }
  }

  return <section className="ref-page skills-page">
    <header className="ref-page-head skills-page-head"><div><span className="ref-eyebrow">SHARED KNOWLEDGE</span><h1>Skills 管理</h1><p>将可复用的方法整理成目录包，Worker 会按任务需要发现并读取其中的 Markdown。</p></div><div className="ref-head-actions"><button className="ref-secondary-button" onClick={() => zipRef.current?.click()} disabled={importing}><Upload size={16} />{importing ? "导入中…" : "导入 ZIP"}</button><button className="ref-primary-button" onClick={() => setCreating(true)}><FolderPlus size={16} />新建 Skill 包</button><input ref={zipRef} hidden type="file" accept=".zip,application/zip" onChange={(event) => void onImport(event)} /></div></header>
    <div className="skill-runtime-note"><strong>唯一数据源：</strong><code>{query.data?.root ?? "runs2/.config/skills"}</code><span>不与场景或角色强绑定；任务提示词可以点名希望 Worker 使用的包。</span></div>
    {message ? <p className="skill-message" role="status">{message}</p> : null}
    <section className="ref-filter-row"><label className="ref-search"><Search size={16} /><input aria-label="搜索 Skill 包" placeholder="搜索包名、说明或标签…" value={search} onChange={(event) => setSearch(event.target.value)} /></label><span className="ref-filter-summary">{skills.length} 个包 · {skills.reduce((sum, item) => sum + item.file_count, 0)} 份文档</span></section>
    {query.isLoading ? <LoadingSkeleton label="正在读取 Skill 包" rows={6} /> : query.isError ? <ErrorState description={query.error instanceof Error ? query.error.message : "无法读取 Skill 包"} actionLabel="重试" onAction={() => void query.refetch()} /> : !skills.length ? <EmptyState title="还没有 Skill 包" description="新建一个包，或导入包含 SKILL.md 的 ZIP。" /> : <div className="skill-package-layout ref-fill"><aside className="skill-package-list-panel" aria-label="Skill 包列表"><header><div><PackageOpen size={18} /><strong>已安装的包</strong></div><span>{skills.length}</span></header><div className="skill-package-list">{skills.map((skill) => <SkillPackageCard key={skill.name} skill={skill} selected={skill.name === selectedName} onSelect={() => setSelectedName(skill.name)} />)}</div></aside>{selectedName ? <SkillPackagePanel key={selectedName} name={selectedName} onMessage={setMessage} onRemoved={() => setSelectedName(null)} /> : null}</div>}
    {creating ? <CreateSkillDialog onClose={() => setCreating(false)} onCreated={async (skill) => { await client.invalidateQueries({ queryKey: ["settings", "skills"] }); setSelectedName(skill.name); setCreating(false); setMessage(`已创建 Skill 包：${skill.name}`); }} /> : null}
  </section>;
}

function SkillPackageCard({ skill, selected, onSelect }: { skill: SkillSetting; selected: boolean; onSelect: () => void }) {
  return <button type="button" className={`skill-package-card${selected ? " active" : ""}`} aria-pressed={selected} onClick={onSelect}>
    <span className="skill-package-card-title"><span className="skill-package-icon"><PackageOpen size={17} /></span><span><strong>{skill.name}</strong><small>v{skill.version}</small></span><i className={skill.enabled ? "online" : ""}>{skill.enabled ? "可检索" : "停用"}</i></span>
    <p>{skill.summary || "这个 Skill 包尚未填写用途说明。"}</p>
    <span className="skill-package-card-tags">{skill.tags.slice(0, 4).map((tag) => <em key={tag}>{tag}</em>)}{skill.tags.length > 4 ? <em>+{skill.tags.length - 4}</em> : null}</span>
    <span className="skill-package-card-meta"><span><FileText size={13} />{skill.file_count} 份文档</span><span><HardDrive size={13} />{byteLabel(skill.total_bytes)}</span></span>
  </button>;
}

function CreateSkillDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (skill: SkillDetail) => void }) {
  const [name, setName] = useState(""); const [description, setDescription] = useState(""); const [tags, setTags] = useState(""); const [instructions, setInstructions] = useState("# Instructions\n\n说明这个 Skill 适用的问题、操作步骤，以及何时读取包内参考文档。"); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function save() { setBusy(true); setError(""); try { onCreated((await createSkill({ name, description, tags: tagsFrom(tags), version: "1", instructions })).skill); } catch (reason) { setError(reason instanceof Error ? reason.message : "创建失败"); } finally { setBusy(false); } }
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="skill-package-dialog" role="dialog" aria-modal="true" aria-labelledby="create-skill-title"><header><div><span>PACKAGE</span><h2 id="create-skill-title">新建 Skill 包</h2><p>系统会在 .config/skills 下创建目录和必需的 SKILL.md。</p></div><button className="icon-button" aria-label="关闭" onClick={onClose}>×</button></header><div className="skill-editor"><label>包名<input aria-label="Skill 包名" value={name} onChange={(event) => setName(event.target.value.toLowerCase())} placeholder="例如 ctf-crypto" /><small>仅小写字母、数字、- 和 _</small></label><label>简介<input aria-label="Skill 简介" value={description} onChange={(event) => setDescription(event.target.value)} /></label><label>标签<input aria-label="Skill 标签" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="ctf, crypto, rsa" /></label><label>SKILL.md Instructions<textarea aria-label="Skill Instructions" rows={10} value={instructions} onChange={(event) => setInstructions(event.target.value)} /></label></div>{error ? <p className="inline-error" role="alert">{error}</p> : null}<footer><button className="secondary-button" disabled={busy} onClick={onClose}>取消</button><button disabled={busy || !name.trim() || !instructions.trim()} onClick={() => void save()}>{busy ? "创建中…" : "创建包"}</button></footer></section></div>;
}

function SkillPackagePanel({ name, onMessage, onRemoved }: { name: string; onMessage: (value: string) => void; onRemoved: () => void }) {
  const client = useQueryClient();
  const detail = useQuery({ queryKey: ["settings", "skills", name], queryFn: () => fetchSkillDetail(name) });
  const [draft, setDraft] = useState<SkillDetail | null>(null); const [selectedPath, setSelectedPath] = useState("SKILL.md"); const [saving, setSaving] = useState(false); const [deletePackageOpen, setDeletePackageOpen] = useState(false); const [deletePath, setDeletePath] = useState<string | null>(null); const documentRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (detail.data) { setDraft(detail.data.skill); setSelectedPath((current) => detail.data.skill.documents.some((item) => item.path === current) ? current : "SKILL.md"); } }, [detail.data]);
  const document = useQuery({ queryKey: ["settings", "skills", name, "document", selectedPath], queryFn: () => fetchSkillDocument(name, selectedPath), enabled: Boolean(selectedPath && selectedPath !== "SKILL.md") });
  if (detail.isError) return <section className="ref-detail-panel"><ErrorState description="无法读取 Skill 包详情" /></section>;
  if (detail.isLoading || !draft) return <section className="ref-detail-panel"><LoadingSkeleton label="正在读取 Skill 包" rows={5} /></section>;
  const currentDraft = draft;

  async function refresh(next?: SkillDetail) { await client.invalidateQueries({ queryKey: ["settings", "skills"] }); await client.invalidateQueries({ queryKey: ["settings", "skills", name] }); if (next) setDraft(next); }
  async function save() { setSaving(true); try { const result = await updateSkill(name, { description: currentDraft.summary, tags: currentDraft.tags, version: currentDraft.version, instructions: currentDraft.instructions }); await refresh(result.skill); onMessage(`已保存 ${name}/SKILL.md`); } catch (reason) { onMessage(reason instanceof Error ? reason.message : "保存失败"); } finally { setSaving(false); } }
  async function addDocument(event: ChangeEvent<HTMLInputElement>) { const file = event.target.files?.[0]; event.target.value = ""; if (!file) return; try { const result = await putSkillDocument(name, { path: file.name, content: await file.text() }); await refresh(result.skill); setSelectedPath(file.name); onMessage(`已写入 ${name}/${file.name}`); } catch (reason) { onMessage(reason instanceof Error ? reason.message : "文档写入失败"); } }
  async function removePackage() { setSaving(true); try { await deleteSkill(name); await refresh(); setDeletePackageOpen(false); onRemoved(); onMessage(`已删除 Skill 包：${name}`); } catch (reason) { onMessage(reason instanceof Error ? reason.message : "删除失败"); } finally { setSaving(false); } }
  async function removeDocument() { if (!deletePath) return; setSaving(true); try { await deleteSkillDocument(name, deletePath); setSelectedPath("SKILL.md"); await refresh(); setDeletePath(null); onMessage(`已删除 ${name}/${deletePath}`); } catch (reason) { onMessage(reason instanceof Error ? reason.message : "删除文档失败"); } finally { setSaving(false); } }

  return <section className="ref-detail-panel skill-package-panel" aria-label={`${name} 详情`}><header className="ref-detail-head"><div><h2>{name}</h2><span className="ref-version-chip">v{draft.version}</span></div><div className="ref-head-actions"><button className="ref-secondary-button" onClick={() => documentRef.current?.click()}><FilePlus2 size={15} />添加 Markdown</button><button className="danger-button" onClick={() => setDeletePackageOpen(true)}><Trash2 size={15} />删除包</button><input ref={documentRef} hidden type="file" accept=".md,text/markdown,text/plain" onChange={(event) => void addDocument(event)} /></div></header><FieldGrid columns={2} fields={[{ label: "入口文件", value: <code>SKILL.md</code> }, { label: "文档", value: `${draft.file_count} 份 / ${byteLabel(draft.total_bytes)}` }, { label: "内容摘要", value: <code>{draft.content_sha256.slice(0, 16)}…</code> }, { label: "标签", value: <ChipList values={draft.tags} tone="neutral" /> }]} /><div className="skill-package-body"><nav aria-label="Skill 文档">{draft.documents.map((item) => <button key={item.path} className={selectedPath === item.path ? "active" : ""} onClick={() => setSelectedPath(item.path)}><span><strong>{item.title}</strong><small>{item.path} · {byteLabel(item.size)}</small></span>{item.path !== "SKILL.md" ? <span role="button" tabIndex={0} aria-label={`删除 ${item.path}`} onClick={(event) => { event.stopPropagation(); setDeletePath(item.path); }}><Trash2 size={13} /></span> : null}</button>)}</nav><div className="skill-document-view">{selectedPath === "SKILL.md" ? <div className="skill-editor"><label>简介<input aria-label="编辑 Skill 简介" value={draft.summary} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} /></label><label>标签<input aria-label="编辑 Skill 标签" value={draft.tags.join(", ")} onChange={(event) => setDraft({ ...draft, tags: tagsFrom(event.target.value) })} /></label><label>版本<input aria-label="编辑 Skill 版本" value={draft.version} onChange={(event) => setDraft({ ...draft, version: event.target.value })} /></label><label>Instructions<textarea aria-label="编辑 Skill Instructions" rows={16} value={draft.instructions} onChange={(event) => setDraft({ ...draft, instructions: event.target.value })} /></label><button className="ref-primary-button" disabled={saving} onClick={() => void save()}><Save size={15} />{saving ? "保存中…" : "保存 SKILL.md"}</button></div> : document.isLoading ? <LoadingSkeleton label="正在读取文档" rows={6} /> : document.isError ? <ErrorState description="无法读取文档" /> : <><header><div><h3>{document.data?.document.title}</h3><code>{selectedPath}</code></div><span>{byteLabel(document.data?.document.size ?? 0)}</span></header><pre className="ref-prompt">{document.data?.document.content}</pre></>}</div></div><ConfirmDialog open={deletePackageOpen} title={`删除 Skill 包 ${name}`} description="该目录及其全部 Markdown 会被删除，正在运行的 Agent 将无法再读取它。" danger busy={saving} confirmLabel="删除整个包" onCancel={() => setDeletePackageOpen(false)} onConfirm={() => void removePackage()} /><ConfirmDialog open={Boolean(deletePath)} title={`删除文档 ${deletePath ?? ""}`} description="该文件会从 Skill 包中永久删除。SKILL.md 不能删除。" danger busy={saving} confirmLabel="删除文档" onCancel={() => setDeletePath(null)} onConfirm={() => void removeDocument()} /></section>;
}
