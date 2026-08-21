import { Save } from "lucide-react";
import { useEffect, useState } from "react";
import { tga3ConfigApi, type ScenesConfig } from "../api/tga3-config";

export function PoliciesPage() {
  const [value, setValue] = useState<ScenesConfig | null>(null);
  const [selectedId, setSelectedId] = useState("penetration_test");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { void tga3ConfigApi.scenes().then(setValue).catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "无法读取场景")); }, []);
  const selected = value?.scenes.find((scene) => scene.id === selectedId) ?? value?.scenes[0] ?? null;
  function prompt(valueText: string) { if (value && selected) setValue({ ...value, scenes: value.scenes.map((scene) => scene.id === selected.id ? { ...scene, system_prompt: valueText } : scene) }); }
  async function save() { if (!value) return; setBusy(true); setMessage(""); try { setValue(await tga3ConfigApi.saveScenes(value)); setMessage("场景提示词已保存，新任务会把所选提示词写入黑板。"); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "保存失败"); } finally { setBusy(false); } }
  return <div className="ref-page scenes-page">
    <header className="ref-page-head"><div><h1>场景</h1><p>为八种固定场景配置任务启动时写入黑板的提示词。</p></div><button className="ref-primary-button" disabled={!value || busy} onClick={() => void save()}><Save size={16} />{busy ? "保存中…" : "保存"}</button></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    <div className="scene-settings-shell ref-card ref-fill">
      <nav className="scene-settings-tabs" aria-label="场景">{(value?.scenes ?? []).map((scene) => <button key={scene.id} className={selected?.id === scene.id ? "selected" : ""} onClick={() => setSelectedId(scene.id)}><strong>{scene.name}</strong><small>{scene.description}</small></button>)}</nav>
      {selected ? <section className="scene-prompt-panel"><header><div><h2>{selected.name}</h2><p>{selected.description}</p></div><span>固定场景</span></header><label>System Prompt<textarea rows={19} value={selected.system_prompt} onChange={(event) => prompt(event.target.value)} /></label><p className="field-help">场景名称、标识和说明是程序定义，不能在配置页面修改。</p></section> : null}
    </div>
  </div>;
}
