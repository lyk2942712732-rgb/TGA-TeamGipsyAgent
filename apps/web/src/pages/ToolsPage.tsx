import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { fetchCapabilities, fetchMCPHealth } from "../api/capabilities";
import type { Capability, MCPCatalog, MCPHealth } from "../runtime/event-types";
import type { MCPManagedServer } from "../runtime/event-types";
import { runtimeApi } from "../runtime/api-v2";
import { EmptyState } from "../components/ui/EmptyState";
import { MCPWizard } from "../components/mcp/MCPWizard";

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
      nextErrors.capabilities = capabilitiesResult.reason instanceof Error ? capabilitiesResult.reason.message : "无法读取内置工具能力";
    }
    if (healthResult.status === "fulfilled") {
      setHealth(healthResult.value);
    } else {
      setHealth(null);
      nextErrors.health = healthResult.reason instanceof Error ? healthResult.reason.message : "无法读取 MCP 服务状态";
    }
    if (serversResult.status === "fulfilled") {
      setManagedServers(serversResult.value.servers);
    } else {
      setManagedServers([]);
      nextErrors.servers = serversResult.reason instanceof Error ? serversResult.reason.message : "无法读取已配置的 MCP 服务";
    }
    setLoadErrors(nextErrors);
    setLoading(false);
  };
  useEffect(() => { void load(); }, []);
  const refresh = async () => { setRefreshing(true); setError(""); try { await Promise.all(managedServers.map((server) => runtimeApi.refreshMCPServer(server.id))); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "MCP 目录刷新失败"); } finally { setRefreshing(false); } };
  const importFile = async (file: File) => {
    setImporting(true);
    setError("");
    setImportMessage(`正在上传 ${file.name}；Docker 镜像加载和 MCP 工具发现可能需要几分钟…`);
    try {
      const result = await runtimeApi.importMCP(file);
      if (result.requires_selection) {
        setImportMessage(`归档中已加载 ${result.images?.length ?? 0} 个镜像标签。请点击“添加 MCP 服务”，并从以下本地镜像中选择：${result.images?.join(", ")}。`);
        await load();
        return;
      }
      const record = result.catalog?.records.find((item) => item.server === result.server_id || item.tool === result.server_id);
      const discovery = record?.discovered ? `已发现 ${record.tools ?? 0} 个工具` : record?.error?.message ?? "配置已写入，等待工具发现";
      setImportMessage(`镜像 ${result.image} 已配置为 ${result.server_id}；${discovery}。`);
      await load();
    } catch (reason) {
      setImportMessage("");
      setError(reason instanceof Error ? reason.message : "MCP 镜像导入失败");
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
    setImportMessage(`正在${enabled ? "启用" : "停用"} ${serverId} 并刷新 MCP 工具发现…`);
    try {
      const result = await runtimeApi.updateMCPServer(serverId, { enabled });
      const record = result.server.status;
      const detail = enabled
        ? record?.discovered ? `已发现 ${record.tools ?? 0} 个工具` : record?.error?.message ?? "已启用，但未发现工具"
        : "已停用，后续任务轮次不会装配该服务";
      setImportMessage(`${serverId}：${detail}。`);
      await load();
    } catch (reason) {
      setImportMessage("");
      setError(reason instanceof Error ? reason.message : "无法修改 MCP 服务状态");
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
      await runtimeApi.deleteMCPServer(serverId);
      setConfirmDelete(null);
      setExpandedServers((current) => { const next = new Set(current); next.delete(serverId); return next; });
      setImportMessage(`${serverId} 已从 mcp.json 移除；对应的本地 Docker 镜像仍保留。`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法删除 MCP 服务");
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
    ? `已配置 ${serverGroups.length} 个服务，发现 ${discoveredMethodCount} 个工具。展开服务可查看其工具。`
    : loading ? "正在读取已配置的 MCP 工具目录…" : "MCP 工具目录暂时无法读取，其他运行时能力仍可使用。";
  return <section className="page-stack settings-page capabilities-settings-page">
    <header className="page-title"><div><span className="eyebrow">配置 / 工具目录</span><h1>工具能力与 MCP</h1><p>内置工具由 TGA 本机运行时提供；MCP 工具来自 <code>mcp.json</code> 允许列表，并通过 <code>initialize</code> 与 <code>tools/list</code> 发现。</p></div><div className="button-row"><button className="secondary-button" disabled={refreshing || importing || switchingServer !== null} onClick={() => void refresh()}>{refreshing ? "正在刷新…" : "刷新目录"}</button><button onClick={() => { setEditingServer(null); setWizardOpen(true); }}>添加 MCP 服务</button></div></header>
    {error ? <div className="inline-error" role="alert">{error}</div> : null}
    {Object.entries(loadErrors).map(([source, message]) => <div className="inline-error" role="alert" key={source}><strong>{source}:</strong> {message}</div>)}
    <section className="surface">
      <div className="surface-head"><div><h2>导入 MCP 镜像</h2><p>拖入由 <code>docker save</code> 创建的镜像归档；TGA 会加载镜像、写入本机允许列表并刷新工具发现。</p></div><span className="schema-chip">本机 Docker</span></div>
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
        <strong>{importing ? "正在加载并配置 MCP 镜像…" : "将 MCP 镜像归档拖到这里"}</strong>
        <span>{importing ? "工具发现完成前请保持此页面打开。" : "也可以点击选择 .tar / .tar.gz / .tgz 文件"}</span>
        <small>不接受源码 ZIP 或 Dockerfile；Docker 参数、挂载和资源限制由 TGA 生成。</small>
      </div>
      {importMessage ? <div className="mcp-import-result" role="status">{importMessage}</div> : null}
    </section>
    <section className="surface builtin-capabilities"><div className="surface-head"><div><h2>内置受控工具</h2><p>由本机运行时直接执行，并按任务的执行边界、并发和超时策略治理。工具标识保留原样，便于与运行日志对应。</p></div><span className="schema-chip">{items.length} 个工具</span></div><div className="capability-grid">{items.map((item) => <article className="capability-card" key={item.name}><div className="capability-title"><div><h3>{item.name}</h3><p>{capabilityDescription(item.name)}</p></div><span className={`status-badge ${item.availability === "available" || item.availability === "healthy" ? "completed" : "blocked"}`}>{availabilityLabel(item.availability)}</span></div><div className="capability-meta"><span>适用场景：{item.modes.map(modeLabel).join(" · ") || "未声明"}</span><small>风险级别：{riskText(item.risk)}</small></div></article>)}</div>{!items.length && !loading ? <EmptyState label="当前后端未返回内置工具能力。" /> : null}</section>
    <section className="surface">
      <div className="surface-head"><div><h2>MCP 工具目录</h2><p>{catalogSummary}</p></div><span className={`status-badge ${catalogState === "healthy" ? "completed" : "blocked"}`}>{availabilityLabel(catalogState)}</span></div>
      {catalog?.reason ? <div className="inline-error">{catalog.reason}</div> : null}
      <div className="mcp-service-list">{serverGroups.map(({ serverId, record, tools }) => {
        const state = record?.enabled === false ? "已停用" : record?.runnable === true ? "运行正常" : record?.runnable === false ? "最近调用失败" : record?.discovered ? "已发现，尚未调用" : record?.reachable ? "可连接，工具发现失败" : record?.configured ? "已配置" : "未知";
        const methods = tools.flatMap((tool) => tool.methods.map((method) => ({ ...method, providerName: tool.provider_name, risk: tool.risk })));
        const expanded = expandedServers.has(serverId);
        const enabled = record?.enabled !== false;
        const switching = switchingServer === serverId;
        const workspaceMode = record?.workspace_access?.mode ?? (record?.image ? "automatic" : record?.transport === "streamable_http" ? "remote" : "host_process");
        return <article className="mcp-service-card" key={serverId}>
          <header>
            <button className="mcp-service-toggle" aria-expanded={expanded} onClick={() => toggleServer(serverId)}><span className="mcp-chevron" aria-hidden="true">{expanded ? "▾" : "▸"}</span><span><strong>{serverId}</strong><small>{methods.length} 个工具</small></span></button>
            <div className="mcp-service-actions"><span className="schema-chip">{record?.transport ?? managedServers.find((item) => item.id === serverId)?.config.transport ?? "stdio"}</span><span className={`status-badge ${record?.runnable === true || (record?.discovered && record?.runnable == null) ? "completed" : "blocked"}`}>{state}</span><button className="secondary-button" disabled={!enabled || testingMethod !== null} onClick={() => void testConnection(serverId)}>测试连接与发现</button><button className="secondary-button" disabled={deleting || importing || refreshing || switchingServer !== null} onClick={() => { setEditingServer(managedServers.find((item) => item.id === serverId) ?? null); setWizardOpen(true); }}>编辑</button><button className="secondary-button mcp-toggle-button" disabled={deleting || importing || refreshing || switchingServer !== null} onClick={() => void changeServerEnabled(serverId, !enabled)}>{switching ? (enabled ? "正在停用…" : "正在启用…") : (enabled ? "停用" : "启用")}</button><button className="danger-button mcp-delete-button" disabled={deleting || importing || refreshing || switchingServer !== null} onClick={() => setConfirmDelete(serverId)}>删除</button></div>
          </header>
          {record?.error?.message ? <div className="mcp-service-error">{record.error.message}</div> : null}
          <div className="mcp-service-detail">任务文件：{workspaceMode === "automatic" ? "真实任务调用时自动挂载 /workspace（输入只读，artifacts 可写）" : workspaceMode === "remote" ? "远程 MCP，通过协议传递文件，不挂载本地目录" : "本地主机进程，文件访问由受控参数决定"}</div>
          {record?.last_call_at ? <div className="mcp-service-error">最后真实调用：{record.last_call_method ?? "unknown"} · {record.last_call_duration_ms ?? 0} ms · {record.last_call_at}{record.last_call_error?.message ? ` · ${record.last_call_error.message}` : ""}</div> : null}
          {expanded ? <div className="mcp-method-list">{methods.length ? methods.map((method) => <article key={method.providerName ?? method.name}><div><strong>{method.name}</strong><span className={`risk-chip ${method.risk}`}>{riskText(method.risk)}</span></div>{method.description ? <p>{method.description}</p> : null}{method.providerName ? <code>{method.providerName}</code> : null}<button className="secondary-button" disabled={!enabled || method.risk === "destructive" || testingMethod !== null} onClick={() => void testMethod(serverId, method.name, method.risk)}>{testingMethod === `${serverId}:${method.name}` ? "执行中…" : "执行方法测试"}</button></article>) : <EmptyState label={record?.enabled === false ? "此 MCP 服务已在 mcp.json 中停用。" : "未在此服务中发现工具。"} />}</div> : null}
        </article>;
      })}</div>
      {catalog && !serverGroups.length ? <EmptyState label="尚未配置 MCP 服务。请导入镜像，或在 mcp.json 中添加服务。" /> : null}
    </section>
    {confirmDelete ? <div className="dialog-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-mcp-title"><h2 id="delete-mcp-title">删除 MCP 服务？</h2><p><strong>{confirmDelete}</strong> 将从 mcp.json 中移除，不再向后续任务轮次提供。对应的本机 Docker 镜像不会删除。</p><div><button className="secondary-button" disabled={deleting} onClick={() => setConfirmDelete(null)}>取消</button><button className="danger-button" disabled={deleting} onClick={() => void removeServer()}>{deleting ? "正在删除…" : "从配置中删除"}</button></div></section></div> : null}
    {wizardOpen ? <MCPWizard initial={editingServer} onClose={() => setWizardOpen(false)} onSaved={load} /> : null}
  </section>;
}

function availabilityLabel(value: string) {
  return ({ healthy: "健康", available: "可用", loading: "加载中", unavailable: "不可用", configured: "已配置" } as Record<string, string>)[value] ?? value;
}

function riskText(value: string) {
  return ({ passive: "被动观察", active: "主动交互", destructive: "高风险" } as Record<string, string>)[value] ?? value;
}

function modeLabel(value: string) {
  return ({ ctf: "CTF 解题", penetration_test: "渗透测试", incident_response: "应急响应", vulnerability_research: "漏洞挖掘", reverse_engineering: "逆向分析" } as Record<string, string>)[value] ?? value;
}

function capabilityDescription(value: string) {
  return ({
    "artifact.inspect": "按范围预览已有证据产物，不执行外部动作。",
    "http.request": "按任务网络边界发送 HTTP 请求，并保存请求与响应证据。",
    "workspace.python": "在隔离任务工作区中运行受限 Python 脚本。",
    "workspace.read": "读取任务工作区内的文件片段。",
    "workspace.shell": "在隔离任务工作区中执行受限 Shell 命令并保存输出。",
    "workspace.write": "向任务工作区写入文件或生成后续分析材料。",
  } as Record<string, string>)[value] ?? "由本机运行时治理并执行的任务工具。";
}
