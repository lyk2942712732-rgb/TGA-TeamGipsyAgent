import { FilePlus2, Save, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { tga3SkillsApi, type SkillSummary } from "../api/tga3-skills";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";

export function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [content, setContent] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [remove, setRemove] = useState(false);
  const [message, setMessage] = useState("");

  async function load(preferred?: string) {
    const rows = await tga3SkillsApi.list();
    setSkills(rows);
    const next = preferred ?? selected ?? rows[0]?.name ?? "";
    setSelected(rows.some((item) => item.name === next) ? next : rows[0]?.name ?? "");
  }

  useEffect(() => { void load().catch((reason: unknown) => setMessage(errorText(reason))); }, []);
  useEffect(() => {
    if (!selected) { setContent(""); return; }
    void tga3SkillsApi.read(selected).then((value) => setContent(value.content)).catch((reason: unknown) => setMessage(errorText(reason)));
  }, [selected]);

  const visible = useMemo(() => skills.filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(search.toLowerCase())), [skills, search]);

  async function save() {
    if (!selected || !content.trim()) return;
    setBusy(true); setMessage("");
    try { await tga3SkillsApi.save(selected, content); await load(selected); setMessage("SKILL.md 已保存。"); }
    catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function create() {
    const name = newName.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) { setMessage("Skill 名称只能使用小写字母、数字、下划线和连字符。"); return; }
    setBusy(true); setMessage("");
    try {
      await tga3SkillsApi.save(name, `# ${name}\n\n在这里填写 Skill 指令。\n`);
      setCreating(false); setNewName(""); await load(name);
    } catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function confirmRemove() {
    if (!selected) return;
    setBusy(true); setMessage("");
    try { await tga3SkillsApi.remove(selected); setRemove(false); setSelected(""); await load(); }
    catch (reason) { setMessage(errorText(reason)); }
    finally { setBusy(false); }
  }

  return <div className="ref-page skills-page">
    <header className="ref-page-head"><div><h1>Skills</h1><p>直接管理 config/skills/&lt;name&gt;/SKILL.md；所有 Agent 按名称发现并按需读取。</p></div><button className="ref-primary-button" onClick={() => setCreating(true)}><FilePlus2 size={16} />新建 Skill</button></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    <div className="skills-native-layout ref-fill">
      <aside className="ref-card skills-native-list">
        <label className="ref-search"><Search size={15} /><input aria-label="搜索 Skills" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称或描述" /></label>
        <nav>{visible.map((skill) => <button key={skill.name} className={selected === skill.name ? "selected" : ""} onClick={() => setSelected(skill.name)}><strong>{skill.name}</strong><small>{skill.description || "无描述"}</small></button>)}</nav>
        {!visible.length ? <EmptyState title="没有 Skill" description="创建一个 SKILL.md 后，Agent 即可按名称读取。" /> : null}
      </aside>
      <section className="ref-card skills-native-editor">
        {selected ? <><header><div><span className="eyebrow">SKILL.md</span><h2>{selected}</h2></div><div className="button-row"><button className="ref-secondary-button" onClick={() => setRemove(true)}><Trash2 size={15} />删除</button><button className="ref-primary-button" disabled={busy || !content.trim()} onClick={() => void save()}><Save size={15} />保存</button></div></header><textarea aria-label="Skill 内容" value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} /></> : <EmptyState title="请选择一个 Skill" />}
      </section>
    </div>
    {creating ? <div className="runtime-modal-backdrop" role="presentation"><section className="runtime-modal" role="dialog" aria-modal="true"><header><h2>新建 Skill</h2></header><label>名称<input autoFocus value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="web-pentest" /></label><footer><button className="ref-secondary-button" onClick={() => setCreating(false)}>取消</button><button className="ref-primary-button" disabled={busy} onClick={() => void create()}>创建</button></footer></section></div> : null}
    <ConfirmDialog open={remove} title={`删除 Skill ${selected}`} description="将删除整个 Skill 目录。" confirmLabel="删除" danger busy={busy} onCancel={() => setRemove(false)} onConfirm={() => void confirmRemove()} />
  </div>;
}

function errorText(reason: unknown) { return reason instanceof Error ? reason.message : "操作失败"; }
