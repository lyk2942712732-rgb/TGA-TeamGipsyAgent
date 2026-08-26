import { FileText, FolderOpen, Plus, Save, Search, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { tga3SkillsApi, type SkillMarkdownFile, type SkillSummary } from "../api/tga3-skills";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";

export function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [files, setFiles] = useState<SkillMarkdownFile[]>([]);
  const [activePath, setActivePath] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [addingFile, setAddingFile] = useState(false);
  const [newFilePath, setNewFilePath] = useState("");
  const [busy, setBusy] = useState(false);
  const [remove, setRemove] = useState(false);
  const [message, setMessage] = useState("");

  async function load(preferred?: string) {
    const rows = await tga3SkillsApi.list();
    setSkills(rows);
    const candidate = preferred ?? selected;
    setSelected(rows.some((item) => item.name === candidate) ? candidate : rows[0]?.name ?? "");
  }

  useEffect(() => { void load().catch((reason: unknown) => setMessage(errorText(reason))); }, []);
  useEffect(() => {
    if (!selected) { setFiles([]); setActivePath(""); return; }
    void tga3SkillsApi.read(selected).then((value) => {
      setFiles(value.files);
      setActivePath((current) => value.files.some((item) => item.path === current)
        ? current
        : value.files.find((item) => item.path.toLowerCase() === "skill.md")?.path ?? value.files[0]?.path ?? "");
    }).catch((reason: unknown) => setMessage(errorText(reason)));
  }, [selected]);

  const visible = useMemo(() => skills.filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(search.toLowerCase())), [skills, search]);
  const activeFile = files.find((item) => item.path === activePath) ?? null;

  async function save() {
    if (!selected || files.some((item) => !item.content.trim())) return;
    setBusy(true); setMessage("");
    try {
      const value = await tga3SkillsApi.save(selected, Object.fromEntries(files.map((item) => [item.path, item.content])));
      setFiles(value.files);
      await load(selected);
      setMessage(`Skill 包已保存，共 ${value.files.length} 个 Markdown 文件。`);
    } catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function create() {
    const name = newName.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) { setMessage("Skill 包名称只能使用小写字母、数字、下划线和连字符。"); return; }
    setBusy(true); setMessage("");
    try {
      await tga3SkillsApi.create(name, { "SKILL.md": `# ${name}\n\n在这里填写 Skill 包的入口指令。\n` });
      setCreating(false); setNewName(""); await load(name);
    } catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  function addFile() {
    const path = normalizeMarkdownPath(newFilePath);
    if (!path) { setMessage("请输入包内有效的 .md 路径，例如 references/checklist.md。"); return; }
    if (files.some((item) => item.path.toLowerCase() === path.toLowerCase())) { setMessage(`文件已存在：${path}`); return; }
    setFiles((current) => [...current, { path, content: `# ${fileTitle(path)}\n\n` }]);
    setActivePath(path); setNewFilePath(""); setAddingFile(false); setMessage("新文件尚未保存，请编辑后保存整个 Skill 包。");
  }

  function removeFile(path: string) {
    if (path.toLowerCase() === "skill.md") return;
    const next = files.filter((item) => item.path !== path);
    setFiles(next);
    setActivePath(next.find((item) => item.path.toLowerCase() === "skill.md")?.path ?? next[0]?.path ?? "");
    setMessage("文件已从编辑区移除，保存 Skill 包后生效。");
  }

  async function confirmRemove() {
    if (!selected) return;
    setBusy(true); setMessage("");
    try { await tga3SkillsApi.remove(selected); setRemove(false); setSelected(""); await load(); }
    catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  return <div className="ref-page skills-page">
    <header className="ref-page-head"><div><h1>Skills</h1><p>一个 Skill 对应 config/skills/ 下的一个目录包；包内全部 Markdown 文件共同构成该 Skill。</p></div><button className="ref-primary-button" onClick={() => setCreating(true)}><FolderOpen size={16} />新建 Skill 包</button></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    <div className="skills-native-layout ref-fill">
      <aside className="ref-card skills-native-list">
        <label className="ref-search"><Search size={15} /><input aria-label="搜索 Skills" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Skill 包" /></label>
        <nav>{visible.map((skill) => <button key={skill.name} className={selected === skill.name ? "selected" : ""} onClick={() => setSelected(skill.name)}><FolderOpen size={16} /><span><strong>{skill.name}</strong><small>{skill.description || "无描述"} · {skill.file_count} 个 Markdown</small></span></button>)}</nav>
        {!visible.length ? <EmptyState title="没有 Skill 包" description="在 config/skills/ 下创建一个包含 SKILL.md 的目录包。" /> : null}
      </aside>
      <section className="ref-card skills-native-editor">
        {selected ? <>
          <header><div><span className="eyebrow">SKILL PACKAGE · {files.length} MARKDOWN</span><h2>{selected}</h2></div><div className="button-row"><button className="ref-secondary-button" onClick={() => setRemove(true)}><Trash2 size={15} />删除包</button><button className="ref-primary-button" disabled={busy || files.some((item) => !item.content.trim())} onClick={() => void save()}><Save size={15} />保存整个包</button></div></header>
          <div className="skill-package-files" role="tablist" aria-label="Skill 包文件">
            {files.map((file) => <div key={file.path} className={activePath === file.path ? "active" : ""}><button role="tab" aria-selected={activePath === file.path} title={file.path} onClick={() => setActivePath(file.path)}><FileText size={14} /><span>{file.path}</span></button>{file.path.toLowerCase() !== "skill.md" ? <button className="skill-file-remove" aria-label={`移除 ${file.path}`} title="从包中移除" onClick={() => removeFile(file.path)}><X size={12} /></button> : null}</div>)}
            <button className="skill-file-add" onClick={() => setAddingFile(true)}><Plus size={14} />添加 Markdown</button>
          </div>
          {activeFile ? <textarea aria-label="Skill 文件内容" value={activeFile.content} onChange={(event) => setFiles((current) => current.map((item) => item.path === activeFile.path ? { ...item, content: event.target.value } : item))} spellCheck={false} /> : null}
        </> : <EmptyState title="请选择一个 Skill 包" />}
      </section>
    </div>
    {creating ? <EditorDialog title="新建 Skill 包" label="目录名称" value={newName} placeholder="ctf-web" busy={busy} onChange={setNewName} onCancel={() => setCreating(false)} onConfirm={() => void create()} /> : null}
    {addingFile ? <EditorDialog title="向 Skill 包添加 Markdown" label="包内路径" value={newFilePath} placeholder="references/checklist.md" busy={false} onChange={setNewFilePath} onCancel={() => setAddingFile(false)} onConfirm={addFile} /> : null}
    <ConfirmDialog open={remove} title={`删除 Skill 包 ${selected}`} description="将递归删除该目录包以及包内的全部文件，此操作不可撤销。" confirmLabel="删除整个包" danger busy={busy} onCancel={() => setRemove(false)} onConfirm={() => void confirmRemove()} />
  </div>;
}

function EditorDialog({ title, label, value, placeholder, busy, onChange, onCancel, onConfirm }: { title: string; label: string; value: string; placeholder: string; busy: boolean; onChange: (value: string) => void; onCancel: () => void; onConfirm: () => void }) {
  return <div className="runtime-modal-backdrop" role="presentation"><section className="runtime-modal" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2></header><label>{label}<input autoFocus value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} onKeyDown={(event) => { if (event.key === "Enter") onConfirm(); }} /></label><footer><button className="ref-secondary-button" onClick={onCancel}>取消</button><button className="ref-primary-button" disabled={busy || !value.trim()} onClick={onConfirm}>创建</button></footer></section></div>;
}

function normalizeMarkdownPath(value: string): string | null {
  const path = value.trim().replace(/\\/g, "/").replace(/^\/+/, "");
  if (!path || !path.toLowerCase().endsWith(".md") || path.split("/").some((part) => !part || part === "." || part === "..")) return null;
  return path;
}
function fileTitle(path: string) { const parts = path.split("/"); return parts[parts.length - 1]?.replace(/\.md$/i, "") ?? "New document"; }
function errorText(reason: unknown) { return reason instanceof Error ? reason.message : "操作失败"; }
