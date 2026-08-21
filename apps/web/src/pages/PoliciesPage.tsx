import { Save, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { tga3ConfigApi, type ScenesConfig } from "../api/tga3-config";

export function PoliciesPage() {
  const [value, setValue] = useState<ScenesConfig | null>(null);
  const [selectedId, setSelectedId] = useState("penetration_test");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { void tga3ConfigApi.scenes().then(setValue).catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "无法读取场景配置")); }, []);
  const rows = useMemo(() => (value?.scenes ?? []).filter((scene) => `${scene.name} ${scene.id} ${scene.description}`.toLowerCase().includes(search.toLowerCase())), [value, search]);
  const selected = value?.scenes.find((scene) => scene.id === selectedId) ?? rows[0] ?? null;
  function patch(change: Partial<NonNullable<typeof selected>>) { if (!value || !selected) return; setValue({ ...value, scenes: value.scenes.map((scene) => scene.id === selected.id ? { ...scene, ...change } : scene) }); }
  async function save() { if (!value) return; setBusy(true); setMessage(""); try { setValue(await tga3ConfigApi.saveScenes(value)); setMessage("已写入 config/scenes.json；新任务会把所选场景提示词写入黑板。"); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "保存失败"); } finally { setBusy(false); } }
  return <div className="ref-page">
    <header className="ref-page-head"><div><h1>场景与提示词</h1><p>在原策略页面维护新建任务所使用的八种场景提示词。</p></div><button className="ref-primary-button" disabled={!value || busy} onClick={() => void save()}><Save size={16} />{busy ? "保存中…" : "保存 scenes.json"}</button></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    <div className="ref-master-detail policies-layout ref-fill"><section className="ref-card"><header className="ref-card-head"><h2>题目场景</h2><span>{rows.length}</span></header><label className="ref-search"><Search size={16} /><input aria-label="搜索场景" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索场景…" /></label><div className="solver-catalog-list">{rows.map((scene) => <button key={scene.id} className={selected?.id === scene.id ? "selected" : ""} onClick={() => setSelectedId(scene.id)}><span><strong>{scene.name}</strong><small>{scene.id}</small></span></button>)}</div></section>
      {selected ? <section className="ref-detail-panel"><header className="ref-detail-head"><div className="ref-detail-title"><div><h2>{selected.name}</h2><p>{selected.id}</p></div></div><span className="ref-chip tone-ok">启用</span></header><h3 className="ref-subhead">新建任务展示</h3><label className="wide">场景名称<input value={selected.name} onChange={(event) => patch({ name: event.target.value })} /></label><label className="wide">场景说明<textarea rows={3} value={selected.description} onChange={(event) => patch({ description: event.target.value })} /></label><h3 className="ref-subhead">场景 System Prompt</h3><textarea className="solver-prompt-editor" rows={18} value={selected.system_prompt} onChange={(event) => patch({ system_prompt: event.target.value })} /><ul className="policy-notes"><li>任务创建时，该提示词连同用户描述和文件索引写入黑板。</li><li>这里不选择模型；Agent 模型只在 Solver 配置页面绑定。</li></ul></section> : null}
    </div>
  </div>;
}
