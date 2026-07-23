import { ChangeEvent, DragEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { fetchCapabilities, fetchMCPHealth } from "../api/capabilities";
import { getLLMSettings, updateLLMSettings, verifyLLMSettings, type LLMSettings } from "../api/tasks";
import type { Capability, MCPCatalog, MCPHealth } from "../runtime/event-types";
import type { MCPManagedServer } from "../runtime/event-types";
import { runtimeApi } from "../runtime/api-v2";
import { EmptyState } from "../components/ui/EmptyState";
import { MCPWizard } from "../components/mcp/MCPWizard";

export function ModelsPage() {
  const [settings, setSettings] = useState<LLMSettings | null>(null); const [verifying, setVerifying] = useState(false);
  const [draft, setDraft] = useState({ base_url: "", api_key: "", model: "" });
  const [message, setMessage] = useState("");
  useEffect(() => { void getLLMSettings().then((value) => { setSettings(value); setDraft({ base_url: value.base_url, api_key: "", model: value.model }); }); }, []);
  const save = async (event: FormEvent) => { event.preventDefault(); try { const next = await updateLLMSettings(draft); setSettings(next); setDraft((current) => ({ ...current, api_key: "" })); setMessage("配置已保存；API Key 不会回显。"); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "保存失败"); } };
  const verify = async () => { setVerifying(true); setMessage(""); try { const result = await verifyLLMSettings(); setMessage(result.reachable && result.action_tools ? `模型连接与工具调用协议验证成功：${result.model}` : "模型未返回有效工具调用。"); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "模型工具调用协议验证失败"); } finally { setVerifying(false); } };
  return <section className="page-stack"><header className="page-title"><div><span className="eyebrow">Settings / Provider & Models</span><h1>Provider 与模型</h1><p>OpenAI-compatible Provider 设置只影响后端请求；前端不保存或回显 API Key。</p></div></header><form className="surface settings-form" onSubmit={save}><span className={`status-badge ${settings?.configured ? "completed" : "blocked"}`}>{settings?.configured ? "已配置" : "未配置"}</span><label>Provider Base URL<input value={draft.base_url} onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} /></label><label>模型 ID<input value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} /></label><label>Provider API Key<input type="password" placeholder={settings?.api_key_set ? "已设置；留空会清空当前环境变量" : "输入 API Key"} value={draft.api_key} onChange={(e) => setDraft({ ...draft, api_key: e.target.value })} /></label>{message ? <p>{message}</p> : null}<div className="button-row"><button>保存设置</button><button type="button" disabled={!settings?.configured || verifying} onClick={() => void verify()}>{verifying ? "正在验证…" : "验证模型连接"}</button></div></form></section>;
}

export function CapabilitiesPage() {
  const [items, setItems] = useState<Capability[]>([]);
  const [catalog, setCatalog] = useState<MCPCatalog | null>(null);
  const [health, setHealth] = useState<MCPHealth | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadErrors, setLoadErrors] = useState<Record<string, string>>({});
  const [refreshing, setRefreshing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [importMessage, setImportMessage] = useState("");
  const [expandedServers, setExpandedServers] = useState<Set<string>>(() => new Set());
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [switchingServer, setSwitchingServer] = useState<string | null>(null);
  const [managedServers, setManagedServers] = useState<MCPManagedServer[]>([]);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [editingServer, setEditingServer] = useState<MCPManagedServer | null>(null);
  const [testingMethod, setTestingMethod] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const load = async () => {
    setLoading(true);
    const [capabilitiesResult, healthResult, serversResult] = await Promise.allSettled([
      fetchCapabilities(), fetchMCPHealth(), runtimeApi.mcpServers(),
    ]);
    const nextErrors: Record<string, string> = {};
    if (capabilitiesResult.status === "fulfilled") {
      setItems(capabilitiesResult.value.capabilities);
      setCatalog(capabilitiesResult.value.tools);
    } else {
      setItems([]);
      setCatalog(null);
      nextErrors.capabilities = capabilitiesResult.reason instanceof Error ? capabilitiesResult.reason.message : "Unable to read runtime capabilities";
    }
    if (healthResult.status === "fulfilled") {
      setHealth(healthResult.value);
    } else {
      setHealth(null);
      nextErrors.health = healthResult.reason instanceof Error ? healthResult.reason.message : "Unable to read MCP health";
    }
    if (serversResult.status === "fulfilled") {
      setManagedServers(serversResult.value.servers);
    } else {
      setManagedServers([]);
      nextErrors.servers = serversResult.reason instanceof Error ? serversResult.reason.message : "Unable to read configured MCP services";
    }
    setLoadErrors(nextErrors);
    setLoading(false);
  };
  useEffect(() => { void load(); }, []);
  const refresh = async () => { setRefreshing(true); setError(""); try { await runtimeApi.refreshMCP(); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "MCP refresh failed"); } finally { setRefreshing(false); } };
  const importFile = async (file: File) => {
    setImporting(true);
    setError("");
    setImportMessage(`Uploading ${file.name}; Docker build/load and MCP discovery may take several minutes…`);
    try {
      const result = await runtimeApi.importMCP(file);
      if (result.requires_selection) {
        setImportMessage(`Archive loaded ${result.images?.length ?? 0} RepoTags. Use “Add MCP service” and choose one of these local images: ${result.images?.join(", ")}.`);
        await load();
        return;
      }
      const record = result.catalog?.records.find((item) => item.server === result.server_id || item.tool === result.server_id);
      const discovery = record?.discovered ? `${record.tools ?? 0} tools discovered` : record?.error?.message ?? "configured; discovery is pending";
      setImportMessage(`${result.image} was ${result.config_action} as ${result.server_id}; ${discovery}.`);
      await load();
    } catch (reason) {
      setImportMessage("");
      setError(reason instanceof Error ? reason.message : "MCP image import failed");
    } finally {
      setImporting(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };
  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void importFile(file);
  };
  const dropFile = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    if (importing) return;
    const file = event.dataTransfer.files?.[0];
    if (file) void importFile(file);
  };
  const changeServerEnabled = async (serverId: string, enabled: boolean) => {
    setSwitchingServer(serverId);
    setError("");
    setImportMessage(`${enabled ? "Enabling" : "Disabling"} ${serverId} and refreshing MCP discovery…`);
    try {
      const result = await runtimeApi.setMCPEnabled(serverId, enabled);
      const record = result.catalog.records.find((item) => item.server === serverId || item.tool === serverId);
      const detail = enabled
        ? record?.discovered ? `${record.tools ?? 0} tools discovered` : record?.error?.message ?? "enabled, but no tools were discovered"
        : "disabled; it will not be offered to new Agent turns";
      setImportMessage(`${serverId}: ${detail}.`);
      await load();
    } catch (reason) {
      setImportMessage("");
      setError(reason instanceof Error ? reason.message : "Unable to change MCP service state");
    } finally {
      setSwitchingServer(null);
    }
  };
  const removeServer = async () => {
    if (!confirmDelete) return;
    const serverId = confirmDelete;
    setDeleting(true);
    setError("");
    try {
      await runtimeApi.deleteMCP(serverId);
      setConfirmDelete(null);
      setExpandedServers((current) => { const next = new Set(current); next.delete(serverId); return next; });
      setImportMessage(`${serverId} was removed from mcp.json. Its local Docker image was kept.`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to delete MCP service");
    } finally {
      setDeleting(false);
    }
  };
  const toggleServer = (serverId: string) => setExpandedServers((current) => {
    const next = new Set(current);
    if (next.has(serverId)) next.delete(serverId); else next.add(serverId);
    return next;
  });
  const testConnection = async (serverId: string) => {
    setTestingMethod(`${serverId}:discovery`); setError("");
    try { const result = await runtimeApi.testMCPServer(serverId); setImportMessage(`${serverId}: 测试连接/发现工具成功，发现 ${result.tools.length} 个方法。`); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "连接与发现测试失败"); }
    finally { setTestingMethod(null); }
  };
  const testMethod = async (serverId: string, method: string, risk: string) => {
    const raw = window.prompt(`执行 ${serverId}.${method} 的 JSON 参数`, "{}");
    if (raw === null) return;
    let argumentsValue: Record<string, unknown>;
    try { argumentsValue = JSON.parse(raw) as Record<string, unknown>; } catch { setError("方法参数必须是 JSON 对象"); return; }
    const confirmActive = risk === "active" ? window.confirm("这是 active 方法。确认执行一次真实 tools/call？") : false;
    if (risk === "active" && !confirmActive) return;
    setTestingMethod(`${serverId}:${method}`); setError("");
    try { const result = await runtimeApi.testMCPMethod(serverId, method, argumentsValue, confirmActive); setImportMessage(`${serverId}.${method}: ${result.ok ? "执行成功" : `执行失败 ${result.error?.code ?? ""}`}；trace ${result.trace_id}；${result.timings.total_ms ?? 0} ms。${result.content_preview.slice(0, 500)}`); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "方法执行测试失败"); }
    finally { setTestingMethod(null); }
  };
  const healthByTool = useMemo(() => new Map((health?.records ?? []).map((record) => [record.server ?? record.tool, record])), [health]);
  const serverGroups = useMemo(() => {
    const serverIds = new Set<string>();
    for (const record of health?.records ?? []) { const id = record.server ?? record.tool; if (id) serverIds.add(id); }
    for (const server of managedServers) serverIds.add(server.id);
    for (const tool of catalog?.tools ?? []) serverIds.add(tool.tool_id);
    return [...serverIds].sort().map((serverId) => ({
      serverId,
      record: healthByTool.get(serverId),
      tools: (catalog?.tools ?? []).filter((tool) => tool.tool_id === serverId),
    }));
  }, [catalog, health, healthByTool, managedServers]);
  const discoveredMethodCount = useMemo(() => serverGroups.reduce((count, group) => count + group.tools.reduce((toolCount, tool) => toolCount + tool.methods.length, 0), 0), [serverGroups]);
  const catalogState = catalog?.availability ?? (loading ? "loading" : "unavailable");
  const catalogSummary = catalog
    ? `${serverGroups.length} configured services · ${discoveredMethodCount} discovered tools. Expand a service to inspect its tools.`
    : loading ? "Loading configured MCP catalog…" : "MCP catalog could not be loaded; other capability data remains available.";
  return <section className="page-stack">
    <header className="page-title"><div><span className="eyebrow">Settings / Capabilities</span><h1>Capabilities and MCP</h1><p>MCP servers come only from the explicit host mcp.json allowlist. Discovery uses initialize and tools/list.</p></div><div className="button-row"><button className="secondary-button" disabled={refreshing || importing || switchingServer !== null} onClick={() => void refresh()}>{refreshing ? "Refreshing…" : "Refresh catalog"}</button><button onClick={() => { setEditingServer(null); setWizardOpen(true); }}>Add MCP service</button></div></header>
    {error ? <div className="inline-error" role="alert">{error}</div> : null}
    {Object.entries(loadErrors).map(([source, message]) => <div className="inline-error" role="alert" key={source}><strong>{source}:</strong> {message}</div>)}
    <section className="surface">
      <div className="surface-head"><div><h2>Import MCP image</h2><p>Drop a Docker image archive created with docker save. TGA loads it, writes the host allowlist, and refreshes discovery.</p></div><span className="schema-chip">local Docker</span></div>
      <input ref={fileInput} hidden type="file" accept=".tar,.tgz,.gz,application/x-tar" onChange={chooseFile} />
      <div
        className={`mcp-drop-zone ${dragActive ? "active" : ""} ${importing ? "busy" : ""}`}
        role="button"
        tabIndex={importing ? -1 : 0}
        aria-disabled={importing}
        onClick={() => { if (!importing) fileInput.current?.click(); }}
        onKeyDown={(event) => { if (!importing && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); fileInput.current?.click(); } }}
        onDragEnter={(event) => { event.preventDefault(); if (!importing) setDragActive(true); }}
        onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragActive(false); }}
        onDrop={dropFile}
      >
        <strong>{importing ? "Building and configuring MCP image…" : "Drop an MCP image file here"}</strong>
        <span>{importing ? "Keep this page open until discovery finishes." : "or click to choose .tar / .tar.gz / .tgz"}</span>
        <small>Source ZIPs and Dockerfiles are rejected. Docker arguments, mounts and resource limits are generated by TGA.</small>
      </div>
      {importMessage ? <div className="mcp-import-result" role="status">{importMessage}</div> : null}
    </section>
    <section className="surface"><h2>Runtime capabilities</h2>{items.map((item) => <article className="capability-card" key={item.name}><div><h3>{item.name}</h3><p>Modes: {item.modes.join(" · ") || "not declared"}</p></div><span className={`status-badge ${item.availability === "available" || item.availability === "healthy" ? "completed" : "blocked"}`}>{item.availability}</span><small>Risk: {item.risk}</small></article>)}</section>
    <section className="surface">
      <div className="surface-head"><div><h2>MCP tool catalog</h2><p>{catalogSummary}</p></div><span className={`status-badge ${catalogState === "healthy" ? "completed" : "blocked"}`}>{catalogState}</span></div>
      {catalog?.reason ? <div className="inline-error">{catalog.reason}</div> : null}
      <div className="mcp-service-list">{serverGroups.map(({ serverId, record, tools }) => {
        const state = record?.enabled === false ? "disabled" : record?.runnable === true ? "runnable" : record?.runnable === false ? "last call failed" : record?.discovered ? "discovered · never called" : record?.reachable ? "reachable · discovery failed" : record?.configured ? "configured" : "unknown";
        const methods = tools.flatMap((tool) => tool.methods.map((method) => ({ ...method, providerName: tool.provider_name, risk: tool.risk })));
        const expanded = expandedServers.has(serverId);
        const enabled = record?.enabled !== false;
        const switching = switchingServer === serverId;
        const workspaceMode = record?.workspace_access?.mode ?? (record?.image ? "automatic" : record?.transport === "streamable_http" ? "remote" : "host_process");
        return <article className="mcp-service-card" key={serverId}>
          <header>
            <button className="mcp-service-toggle" aria-expanded={expanded} onClick={() => toggleServer(serverId)}><span className="mcp-chevron" aria-hidden="true">{expanded ? "▾" : "▸"}</span><span><strong>{serverId}</strong><small>{methods.length} tool{methods.length === 1 ? "" : "s"}</small></span></button>
            <div className="mcp-service-actions"><span className="schema-chip">{record?.transport ?? managedServers.find((item) => item.id === serverId)?.config.transport ?? "stdio"}</span><span className={`status-badge ${record?.runnable === true || (record?.discovered && record?.runnable == null) ? "completed" : "blocked"}`}>{state}</span><button className="secondary-button" disabled={!enabled || testingMethod !== null} onClick={() => void testConnection(serverId)}>测试连接/发现工具</button><button className="secondary-button" disabled={deleting || importing || refreshing || switchingServer !== null} onClick={() => { setEditingServer(managedServers.find((item) => item.id === serverId) ?? null); setWizardOpen(true); }}>Edit</button><button className="secondary-button mcp-toggle-button" disabled={deleting || importing || refreshing || switchingServer !== null} onClick={() => void changeServerEnabled(serverId, !enabled)}>{switching ? (enabled ? "Disabling…" : "Enabling…") : (enabled ? "Disable" : "Enable")}</button><button className="danger-button mcp-delete-button" disabled={deleting || importing || refreshing || switchingServer !== null} onClick={() => setConfirmDelete(serverId)}>Delete</button></div>
          </header>
          {record?.error?.message ? <div className="mcp-service-error">{record.error.message}</div> : null}
          <div className="mcp-service-detail">任务文件：{workspaceMode === "automatic" ? "真实任务调用时自动挂载 /workspace（输入只读，artifacts 可写）" : workspaceMode === "remote" ? "远程 MCP，通过协议传递文件，不挂载本地目录" : "本地主机进程，文件访问由受控参数决定"}</div>
          {record?.last_call_at ? <div className="mcp-service-error">最后真实调用：{record.last_call_method ?? "unknown"} · {record.last_call_duration_ms ?? 0} ms · {record.last_call_at}{record.last_call_error?.message ? ` · ${record.last_call_error.message}` : ""}</div> : null}
          {expanded ? <div className="mcp-method-list">{methods.length ? methods.map((method) => <article key={method.providerName ?? method.name}><div><strong>{method.name}</strong><span className={`risk-chip ${method.risk}`}>{method.risk}</span></div>{method.description ? <p>{method.description}</p> : null}{method.providerName ? <code>{method.providerName}</code> : null}<button className="secondary-button" disabled={!enabled || method.risk === "destructive" || testingMethod !== null} onClick={() => void testMethod(serverId, method.name, method.risk)}>{testingMethod === `${serverId}:${method.name}` ? "执行中…" : "执行方法测试"}</button></article>) : <EmptyState label={record?.enabled === false ? "This MCP service is disabled in mcp.json." : "No tools were discovered for this service."} />}</div> : null}
        </article>;
      })}</div>
      {catalog && !serverGroups.length ? <EmptyState label="No MCP services configured. Import an image above or add a server to mcp.json." /> : null}
    </section>
    {confirmDelete ? <div className="dialog-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-mcp-title"><h2 id="delete-mcp-title">Delete MCP service?</h2><p><strong>{confirmDelete}</strong> will be removed from mcp.json and disappear from future Agent turns. The local Docker image will not be deleted.</p><div><button className="secondary-button" disabled={deleting} onClick={() => setConfirmDelete(null)}>Cancel</button><button className="danger-button" disabled={deleting} onClick={() => void removeServer()}>{deleting ? "Deleting…" : "Delete from config"}</button></div></section></div> : null}
    {wizardOpen ? <MCPWizard initial={editingServer} onClose={() => setWizardOpen(false)} onSaved={load} /> : null}
  </section>;
}

export { SkillsPage } from "./SkillsPage";
