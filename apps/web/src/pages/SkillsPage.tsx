import { FileArchive, FileText, FolderOpen, Pencil, Plus, Save, Search, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { tga3SkillsApi, type SkillMarkdownFile, type SkillSummary } from "../api/tga3-skills";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";

export function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [files, setFiles] = useState<SkillMarkdownFile[]>([]);
  const [activePath, setActivePath] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [search, setSearch] = useState("");
  const [addingPackage, setAddingPackage] = useState(false);
  const [newName, setNewName] = useState("");
  const [draggingArchive, setDraggingArchive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [remove, setRemove] = useState(false);
  const [message, setMessage] = useState("");
  const markdownInput = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);

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
  const entryFile = activePath.toLowerCase() === "skill.md";
  useEffect(() => { setRenameValue(activeFile ? fileName(activeFile.path) : ""); }, [activePath, selected]);

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

  async function createEmptyPackage() {
    const name = newName.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(name)) {
      setMessage("目录名称须以字母或数字开头，只能包含字母、数字、点、下划线和连字符。");
      return;
    }
    setBusy(true); setMessage("");
    try {
      await tga3SkillsApi.create(name, { "SKILL.md": `# ${name}\n\n在这里填写 Skill 包的入口说明，并指向需要按需读取的其他 Markdown 文件。\n` });
      setAddingPackage(false); setNewName(""); await load(name);
      setMessage(`已创建 Skill 包 ${name}。`);
    } catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function importArchive(file: File) {
    if (!file.name.toLowerCase().endsWith(".zip")) { setMessage("请选择一个 .zip 格式的 Skill 包。"); return; }
    setBusy(true); setMessage("");
    try {
      const imported = await tga3SkillsApi.importArchive(file);
      setAddingPackage(false); setNewName(""); await load(imported.name);
      setMessage(`已导入 Skill 包 ${imported.name}，共 ${imported.files.length} 个 Markdown 文件。`);
    } catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function addMarkdown(uploaded: File[]) {
    const markdown = uploaded.filter((file) => file.name.toLowerCase().endsWith(".md"));
    if (!markdown.length) { setMessage("请选择 Markdown（.md）文件。"); return; }
    const existing = new Set(files.map((item) => fileName(item.path).toLowerCase()));
    const accepted: SkillMarkdownFile[] = [];
    const skipped: string[] = [];
    for (const file of markdown) {
      const name = normalizeMarkdownName(file.name);
      if (!name || existing.has(name.toLowerCase())) { skipped.push(file.name); continue; }
      existing.add(name.toLowerCase());
      accepted.push({ path: name, content: await file.text() });
    }
    if (!accepted.length) { setMessage(`没有添加文件；同名或无效文件：${skipped.join("、")}`); return; }
    setFiles((current) => sortFiles([...current, ...accepted]));
    setActivePath(accepted[0].path);
    setMessage(`${accepted.length} 个 Markdown 文件已加入编辑区${skipped.length ? `；已跳过：${skipped.join("、")}` : ""}，保存整个包后生效。`);
    if (markdownInput.current) markdownInput.current.value = "";
  }

  function renameActiveFile() {
    if (!activeFile || entryFile) return;
    const name = normalizeMarkdownName(renameValue);
    if (!name) { setMessage("文件名必须以 .md 结尾，且不能包含目录分隔符。"); return; }
    const parent = parentPath(activeFile.path);
    const nextPath = parent ? `${parent}/${name}` : name;
    if (files.some((item) => item.path !== activeFile.path && fileName(item.path).toLowerCase() === name.toLowerCase())) {
      setMessage(`当前 Skill 包中已经存在文件 ${name}。`); return;
    }
    setFiles((current) => current.map((item) => item.path === activeFile.path ? { ...item, path: nextPath } : item));
    setActivePath(nextPath);
    setMessage("文件名已在编辑区修改，保存整个包后生效。");
  }

  function removeFile(path: string) {
    if (path.toLowerCase() === "skill.md") return;
    const next = files.filter((item) => item.path !== path);
    setFiles(next);
    setActivePath(next.find((item) => item.path.toLowerCase() === "skill.md")?.path ?? next[0]?.path ?? "");
    setMessage("文件已从编辑区移除，保存整个包后生效。");
  }

  async function confirmRemove() {
    if (!selected) return;
    setBusy(true); setMessage("");
    try { await tga3SkillsApi.remove(selected); setRemove(false); setSelected(""); await load(); }
    catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  function pageDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!hasFiles(event)) return;
    event.preventDefault(); dragDepth.current += 1; setDraggingArchive(true);
  }
  function pageDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!hasFiles(event)) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (!dragDepth.current) setDraggingArchive(false);
  }
  function pageDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); dragDepth.current = 0; setDraggingArchive(false);
    const archive = Array.from(event.dataTransfer.files).find((file) => file.name.toLowerCase().endsWith(".zip"));
    if (archive) void importArchive(archive);
    else setMessage("拖入的文件中没有 .zip 格式的 Skill 包。");
  }

  return <div className="ref-page skills-page" onDragEnter={pageDragEnter} onDragOver={(event) => { if (hasFiles(event)) event.preventDefault(); }} onDragLeave={pageDragLeave} onDrop={pageDrop}>
    <header className="ref-page-head"><div><h1>Skills</h1><p>一个 Skill 对应 config/skills/ 下的一个目录包；Agent 首先读取 SKILL.md，再按需读取包内其他 Markdown。</p></div><button className="ref-primary-button" onClick={() => setAddingPackage(true)}><FolderOpen size={16} />添加 Skill 包</button></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    <div className="skills-native-layout ref-fill">
      <aside className="ref-card skills-native-list">
        <label className="ref-search"><Search size={15} /><input aria-label="搜索 Skills" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Skill 包" /></label>
        <nav>{visible.map((skill) => <button key={skill.name} className={selected === skill.name ? "selected" : ""} onClick={() => setSelected(skill.name)}><FolderOpen size={16} /><span><strong>{skill.name}</strong><small>{skill.description || "无描述"} · {skill.file_count} 个 Markdown</small></span></button>)}</nav>
        {!visible.length ? <EmptyState title="没有 Skill 包" description="点击“添加 Skill 包”创建目录，或直接拖入一个 ZIP。" /> : null}
      </aside>
      <section className="ref-card skills-native-editor">
        {selected ? <>
          <header><div><span className="eyebrow">SKILL PACKAGE · {files.length} MARKDOWN</span><h2>{selected}</h2></div><div className="button-row"><button className="ref-secondary-button" onClick={() => setRemove(true)}><Trash2 size={15} />删除包</button><button className="ref-primary-button" disabled={busy || files.some((item) => !item.content.trim())} onClick={() => void save()}><Save size={15} />保存整个包</button></div></header>
          <div className="skill-package-files" role="tablist" aria-label="Skill 包文件">
            {files.map((file) => <div key={file.path} className={activePath === file.path ? "active" : ""}><button role="tab" aria-selected={activePath === file.path} onClick={() => setActivePath(file.path)}><FileText size={14} /><span>{fileName(file.path)}</span></button></div>)}
            <button className="skill-file-add" onClick={() => markdownInput.current?.click()}><Plus size={14} />上传 Markdown</button>
            <input ref={markdownInput} className="visually-hidden" aria-label="上传 Markdown 文件" type="file" accept=".md,text/markdown" multiple onChange={(event) => void addMarkdown(Array.from(event.target.files ?? []))} />
          </div>
          {activeFile ? <>
            <div className="skill-file-toolbar">
              <label>文件名<input aria-label="文件名" disabled={entryFile} value={renameValue} onChange={(event) => setRenameValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") renameActiveFile(); }} /></label>
              {entryFile ? <span>SKILL.md 是固定入口文件</span> : <><button className="ref-secondary-button" disabled={renameValue === fileName(activeFile.path)} onClick={renameActiveFile}><Pencil size={14} />应用文件名</button><button className="skill-current-delete" onClick={() => removeFile(activeFile.path)}><Trash2 size={14} />删除文件</button></>}
            </div>
            <textarea aria-label="Skill 文件内容" value={activeFile.content} onChange={(event) => setFiles((current) => current.map((item) => item.path === activeFile.path ? { ...item, content: event.target.value } : item))} spellCheck={false} />
          </> : null}
        </> : <EmptyState title="请选择一个 Skill 包" description="也可以把 ZIP Skill 包直接拖到此页面。" />}
      </section>
    </div>
    {addingPackage ? <AddSkillDialog name={newName} busy={busy} onNameChange={setNewName} onCreate={() => void createEmptyPackage()} onImport={(file) => void importArchive(file)} onCancel={() => setAddingPackage(false)} /> : null}
    <ConfirmDialog open={remove} title={`删除 Skill 包 ${selected}`} description="将递归删除该目录包以及包内全部文件，此操作不可撤销。" confirmLabel="删除整个包" danger busy={busy} onCancel={() => setRemove(false)} onConfirm={() => void confirmRemove()} />
    {draggingArchive ? <div className="skill-page-drop-overlay" aria-hidden="true"><FileArchive size={34} /><strong>松开以添加 Skill 包</strong><span>ZIP 将安全解压到 config/skills/</span></div> : null}
  </div>;
}

function AddSkillDialog({ name, busy, onNameChange, onCreate, onImport, onCancel }: { name: string; busy: boolean; onNameChange: (value: string) => void; onCreate: () => void; onImport: (file: File) => void; onCancel: () => void }) {
  const [dragging, setDragging] = useState(false);
  return <div className="runtime-modal-backdrop" role="presentation" onDragEnter={(event) => event.stopPropagation()} onDragOver={(event) => event.stopPropagation()} onDragLeave={(event) => event.stopPropagation()} onDrop={(event) => event.stopPropagation()}><section className="runtime-modal skill-package-dialog" role="dialog" aria-modal="true" aria-label="添加 Skill 包">
    <header><h2>添加 Skill 包</h2><p>创建一个新目录，或导入现有 ZIP Skill 包。</p></header>
    <div className="skill-package-create-row"><label>目录名称<input autoFocus value={name} onChange={(event) => onNameChange(event.target.value)} placeholder="ctf-web" onKeyDown={(event) => { if (event.key === "Enter") onCreate(); }} /></label><button className="ref-primary-button" disabled={busy || !name.trim()} onClick={onCreate}><FolderOpen size={15} />创建空包</button></div>
    <div className="skill-dialog-divider"><span>或者</span></div>
    <label className="skill-archive-dropzone" data-dragging={dragging} onDragEnter={(event) => { event.preventDefault(); setDragging(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); event.stopPropagation(); setDragging(false); const file = Array.from(event.dataTransfer.files).find((item) => item.name.toLowerCase().endsWith(".zip")); if (file) onImport(file); }}>
      <input className="visually-hidden" aria-label="导入 ZIP Skill 包" type="file" accept=".zip,application/zip" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) onImport(file); }} />
      <Upload size={24} /><strong>{busy ? "正在处理…" : "拖入 ZIP，或点击选择"}</strong><span>自动使用 ZIP 顶层目录名；没有顶层目录时使用 ZIP 文件名。</span>
    </label>
    <footer><button className="ref-secondary-button" disabled={busy} onClick={onCancel}>取消</button></footer>
  </section></div>;
}

function sortFiles(files: SkillMarkdownFile[]) {
  return [...files].sort((left, right) => Number(right.path.toLowerCase() === "skill.md") - Number(left.path.toLowerCase() === "skill.md") || left.path.localeCompare(right.path));
}
function normalizeMarkdownName(value: string): string | null {
  const name = value.trim();
  if (!name || name.includes("/") || name.includes("\\") || !name.toLowerCase().endsWith(".md") || name === "." || name === "..") return null;
  return name;
}
function fileName(path: string) { const parts = path.split("/"); return parts[parts.length - 1] ?? path; }
function parentPath(path: string) { return path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : ""; }
function hasFiles(event: DragEvent<HTMLElement>) { return Array.from(event.dataTransfer.types).includes("Files"); }
function errorText(reason: unknown) { return reason instanceof Error ? reason.message : "操作失败"; }
