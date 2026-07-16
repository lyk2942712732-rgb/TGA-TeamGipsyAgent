import { FormEvent, useEffect, useMemo, useState } from "react";
import { fetchCapabilities, fetchMCPHealth } from "../api/capabilities";
import { getLLMSettings, updateLLMSettings, type LLMSettings } from "../api/tasks";
import type { Capability, MCPCatalog, MCPHealth } from "../runtime/event-types";
import { EmptyState } from "../components/ui/EmptyState";

export function ModelsPage() {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [draft, setDraft] = useState({ base_url: "", api_key: "", model: "" });
  const [message, setMessage] = useState("");
  useEffect(() => { void getLLMSettings().then((value) => { setSettings(value); setDraft({ base_url: value.base_url, api_key: "", model: value.model }); }); }, []);
  const save = async (event: FormEvent) => { event.preventDefault(); try { const next = await updateLLMSettings(draft); setSettings(next); setDraft((current) => ({ ...current, api_key: "" })); setMessage("配置已保存；API Key 不会回显。"); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "保存失败"); } };
  return <section className="page-stack"><header className="page-title"><div><span className="eyebrow">Settings / Models</span><h1>模型配置</h1><p>模型设置只影响后端请求；前端不保存或回显 API Key。</p></div></header><form className="surface settings-form" onSubmit={save}><span className={`status-badge ${settings?.configured ? "completed" : "blocked"}`}>{settings?.configured ? "已配置" : "未配置"}</span><label>Base URL<input value={draft.base_url} onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} /></label><label>模型<input value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} /></label><label>API Key<input type="password" placeholder={settings?.api_key_set ? "已设置；留空会清空当前环境变量" : "输入 API Key"} value={draft.api_key} onChange={(e) => setDraft({ ...draft, api_key: e.target.value })} /></label>{message ? <p>{message}</p> : null}<button>保存设置</button></form></section>;
}

export function CapabilitiesPage() {
  const [items, setItems] = useState<Capability[]>([]);
  const [catalog, setCatalog] = useState<MCPCatalog | null>(null);
  const [health, setHealth] = useState<MCPHealth | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void Promise.all([fetchCapabilities(), fetchMCPHealth()]).then(([capabilities, snapshot]) => { setItems(capabilities.capabilities); setCatalog(capabilities.tools); setHealth(snapshot); }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取能力或 MCP 状态")); }, []);
  const healthByTool = useMemo(() => new Map((health?.records ?? []).map((record) => [record.tool, record])), [health]);
  return <section className="page-stack"><header className="page-title"><div><span className="eyebrow">Settings / Capabilities</span><h1>能力与 MCP</h1><p>仅展示服务端注册的能力、项目内 MCP catalog 和 Docker 镜像健康状态；此页面不能执行工具。</p></div></header><section className="surface">{error ? <div className="inline-error">{error}</div> : null}<h2>Runtime 能力</h2>{items.map((item) => <article className="capability-card" key={item.name}><div><h3>{item.name}</h3><p>支持模式：{item.modes.join(" · ") || "未声明"}</p></div><span className={`status-badge ${item.availability === "available" || item.availability === "healthy" ? "completed" : "blocked"}`}>{item.availability}</span><small>风险：{item.risk}</small></article>)}{!items.length && !error ? <EmptyState label="正在读取 Runtime 能力注册表…" /> : null}</section><section className="surface"><div className="surface-head"><div><h2>MCP 工具目录</h2><p>{catalog ? `已注册 ${catalog.tools.length} 个 MCP Server。镜像状态由本机 Docker 检查结果决定。` : "正在读取项目内 mcp-security-hub…"}</p></div><span className={`status-badge ${catalog?.availability === "healthy" ? "completed" : "blocked"}`}>{catalog?.availability ?? "loading"}</span></div>{catalog?.reason ? <div className="inline-error">{catalog.reason}</div> : null}{catalog?.tools.map((tool) => { const record = healthByTool.get(tool.tool_id); const status = record?.status ?? "unknown"; return <article className="capability-card" key={tool.tool_id}><div><h3>{tool.tool_id}</h3><p>{tool.methods.length ? `方法：${tool.methods.map((method) => method.name).join(" · ")}` : "该 Server 未公开可调用方法"}</p>{record?.detail ? <small>{record.detail}</small> : null}</div><span className={`status-badge ${status === "available" ? "completed" : status === "missing" ? "blocked" : "failed"}`}>{status}</span><small>风险：{tool.risk}</small></article>; })}{catalog && !catalog.tools.length ? <EmptyState label="未发现 MCP Server；请检查项目内 mcp-security-hub 目录。" /> : null}{health && !health.configured ? <EmptyState label="MCP Hub 未配置。可在项目根目录放置 mcp-security-hub，或设置 TGA_MCP_SECURITY_HUB_ROOT。" /> : null}</section></section>;
}

export function SkillsPage() { return <section className="page-stack"><header className="page-title"><div><span className="eyebrow">Settings / Skills</span><h1>Skills</h1><p>Skill 的适用模式和加载状态应由后端 registry 提供。</p></div></header><section className="surface"><EmptyState label="当前 v2 API 尚未公开 Skills registry；前端不会猜测本地文件或伪造加载状态。" /></section></section>; }
