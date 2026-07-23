import { ChangeEvent, DragEvent, useRef, useState } from "react";
import { runtimeApi } from "../../runtime/api-v2";
import type { MCPManagedServer, MCPServerTools } from "../../runtime/event-types";

type Props = { initial?: MCPManagedServer | null; onClose: () => void; onSaved: () => Promise<void> | void };

export function MCPWizard({ initial, onClose, onSaved }: Props) {
  const initialHttp = initial?.config.http;
  const initialStdio = initial?.config.stdio;
  const [step, setStep] = useState(initial ? 2 : 1);
  const [transport, setTransport] = useState<"stdio" | "streamable_http">(initial?.config.transport ?? "stdio");
  const [serverId, setServerId] = useState(initial?.id ?? "");
  const [image, setImage] = useState(initialStdio?.image ?? "");
  const [url, setUrl] = useState(initialHttp?.url ?? "");
  const [verifyTls, setVerifyTls] = useState(initialHttp?.verifyTls ?? true);
  const [proxyUrl, setProxyUrl] = useState(initialHttp?.proxyUrl ?? "");
  const [allowRedirects, setAllowRedirects] = useState(initialHttp?.allowSameOriginRedirects ?? false);
  const [memory, setMemory] = useState(initialStdio?.docker?.memory ?? "512m");
  const [cpus, setCpus] = useState(initialStdio?.docker?.cpus ?? 1);
  const [pidsLimit, setPidsLimit] = useState(initialStdio?.docker?.pidsLimit ?? 256);
  const [network, setNetwork] = useState(initialStdio?.docker?.network ?? "none");
  const [readOnly, setReadOnly] = useState(initialStdio?.docker?.readOnly ?? true);
  const [secretHeader, setSecretHeader] = useState(Object.keys(initialHttp?.secretRefs ?? {})[0] ?? "Authorization");
  const [secretEnv, setSecretEnv] = useState(Object.values(initialHttp?.secretRefs ?? {})[0]?.replace(/^env:/, "") ?? "");
  const [enableOnSave, setEnableOnSave] = useState(initial?.config.enabled ?? true);
  const [tools, setTools] = useState<MCPServerTools | null>(null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set(initial?.config.enabledTools ?? []));
  const [candidateImages, setCandidateImages] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadController = useRef<AbortController | null>(null);

  const normalizedId = serverId.trim();
  const persistConnection = async () => {
    if (!/^[A-Za-z0-9_-]+$/.test(normalizedId)) throw new Error("服务 ID 只能包含字母、数字、下划线和连字符");
    if (transport === "stdio") {
      if (!image.trim()) throw new Error("请选择或输入本地 Docker 镜像名");
      await runtimeApi.inspectMCPImage(image.trim());
      const config = {
        enabled: false,
        transport: "stdio" as const,
        enabledTools: initial?.config.enabledTools ?? [],
        stdio: { source: "docker_image" as const, image: image.trim(), docker: { memory, cpus, pidsLimit, network, readOnly, capDropAll: true, noNewPrivileges: true } },
        http: null,
      };
      if (initial) await runtimeApi.updateMCPServer(normalizedId, config);
      else await runtimeApi.createMCPServer(normalizedId, config);
    } else {
      if (!url.trim()) throw new Error("请输入 MCP Streamable HTTP URL");
      const secretRefs = secretEnv.trim() ? { [secretHeader.trim() || "Authorization"]: `env:${secretEnv.trim()}` } : {};
      const config = {
        enabled: false,
        transport: "streamable_http" as const,
        enabledTools: initial?.config.enabledTools ?? [],
        http: { url: url.trim(), verifyTls, secretRefs, proxyUrl: proxyUrl.trim() || null, allowSameOriginRedirects: allowRedirects },
        stdio: null,
      };
      if (initial) await runtimeApi.updateMCPServer(normalizedId, config);
      else await runtimeApi.createMCPServer(normalizedId, config);
    }
  };

  const continueConnection = async () => {
    setBusy(true); setError("");
    try { await persistConnection(); setStep(3); } catch (reason) { setError(reason instanceof Error ? reason.message : "连接配置无效"); } finally { setBusy(false); }
  };

  const testConnection = async () => {
    setBusy(true); setError("");
    try {
      const result = await runtimeApi.testMCPServer(normalizedId);
      setTools(result);
      if (result.status !== "discovered") throw new Error(result.error?.message ?? "未能发现 MCP 工具");
      setSelected((current) => current.size ? current : new Set(result.tools.map((tool) => tool.name)));
      setStep(4);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "MCP 连接测试失败"); } finally { setBusy(false); }
  };

  const finish = async () => {
    setBusy(true); setError("");
    try {
      await runtimeApi.updateMCPServer(normalizedId, { enabled: enableOnSave, enabledTools: [...selected] });
      await onSaved(); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存 MCP 服务失败"); } finally { setBusy(false); }
  };

  const upload = async (file: File) => {
    setBusy(true); setError(""); setProgress(5);
    const controller = new AbortController();
    uploadController.current = controller;
    try {
      const result = await runtimeApi.importMCP(file, setProgress, controller.signal);
      setProgress(100);
      if (result.requires_selection && result.images?.length) {
        setCandidateImages(result.images); setImage(result.images[0]);
        if (!normalizedId) setServerId(result.images[0].split("/").pop()?.split(":")[0].replace(/-mcp$/, "") ?? "imported-mcp");
      } else {
        setImage(result.image); setServerId(result.server_id); setStep(3);
      }
    } catch (reason) {
      setProgress(0);
      setError(reason instanceof DOMException && reason.name === "AbortError" ? "镜像导入已取消" : reason instanceof Error ? reason.message : "镜像导入失败");
    } finally { uploadController.current = null; setBusy(false); }
  };
  const choose = (event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (file) void upload(file); };
  const drop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); const file = event.dataTransfer.files?.[0]; if (file && !busy) void upload(file); };

  return <div className="dialog-backdrop mcp-wizard-backdrop" role="presentation">
    <section className="mcp-wizard" role="dialog" aria-modal="true" aria-labelledby="mcp-wizard-title">
      <header><div><span className="eyebrow">MCP 服务向导 · 第 {step}/4 步</span><h2 id="mcp-wizard-title">{initial ? `编辑 ${initial.id}` : "添加 MCP 服务"}</h2></div><button aria-label="关闭 MCP 向导" onClick={onClose}>×</button></header>
      <ol className="mcp-wizard-steps">{["传输方式", "连接参数", "连接测试", "工具与保存"].map((label, index) => <li className={step >= index + 1 ? "active" : ""} key={label}><b>{index + 1}</b><span>{label}</span></li>)}</ol>
      {step === 1 ? <div className="mcp-choice-grid">
        <button className={transport === "stdio" ? "selected" : ""} onClick={() => setTransport("stdio")}><strong>STDIO / Docker</strong><span>导入 docker save 归档，或使用已经存在的本地镜像。</span></button>
        <button className={transport === "streamable_http" ? "selected" : ""} onClick={() => setTransport("streamable_http")}><strong>Streamable HTTP</strong><span>连接支持 JSON 或 SSE 的远程 MCP endpoint。</span></button>
      </div> : null}
      {step === 2 ? <div className="mcp-wizard-form">
        <label>服务 ID<input value={serverId} onChange={(event) => setServerId(event.target.value)} placeholder="例如 burp-suite" disabled={Boolean(initial)} /></label>
        {transport === "stdio" ? <>
          <label>本地镜像名<input value={image} onChange={(event) => setImage(event.target.value)} placeholder="repository/mcp-server:tag" /></label>
          {candidateImages.length > 1 ? <label>归档包含多个 RepoTag<select value={image} onChange={(event) => setImage(event.target.value)}>{candidateImages.map((item) => <option key={item}>{item}</option>)}</select></label> : null}
          <div className="mcp-field-pair"><label>内存限制<input value={memory ?? ""} onChange={(event) => setMemory(event.target.value)} placeholder="512m" /></label><label>CPU 限制<input type="number" min="0.1" step="0.1" value={cpus ?? 1} onChange={(event) => setCpus(Number(event.target.value))} /></label></div>
          <div className="mcp-field-pair"><label>PID 限制<input type="number" min="1" value={pidsLimit ?? 256} onChange={(event) => setPidsLimit(Number(event.target.value))} /></label><label>Docker 网络<select value={network} onChange={(event) => setNetwork(event.target.value)}><option value="none">none（隔离）</option><option value="bridge">bridge</option></select></label></div>
          <label className="mcp-inline-check"><input type="checkbox" checked={readOnly} onChange={(event) => setReadOnly(event.target.checked)} />只读根文件系统</label>
          <input ref={fileInput} hidden type="file" accept=".tar,.tar.gz,.tgz,application/x-tar" onChange={choose} />
          <div className={`mcp-drop-zone compact ${busy ? "busy" : ""}`} role="button" tabIndex={0} onClick={() => !busy && fileInput.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={drop}><strong>{busy ? `正在上传/加载 Docker 镜像… ${progress}%` : "拖入 docker save 镜像归档"}</strong><span>.tar / .tar.gz / .tgz；不接受 Dockerfile 或源码 ZIP</span>{progress > 0 ? <progress max="100" value={progress}>{progress}%</progress> : null}{busy && uploadController.current ? <button className="danger-button" onClick={(event) => { event.stopPropagation(); uploadController.current?.abort(); }}>取消导入</button> : null}</div>
        </> : <>
          <label>Streamable HTTP URL<input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://mcp.example.com/mcp" /></label>
          <label className="mcp-inline-check"><input type="checkbox" checked={verifyTls} onChange={(event) => setVerifyTls(event.target.checked)} />校验 TLS 证书（推荐且默认开启）</label>
          <label>显式代理 URL（可选）<input value={proxyUrl ?? ""} onChange={(event) => setProxyUrl(event.target.value)} placeholder="http://127.0.0.1:8080" /></label>
          <label className="mcp-inline-check"><input type="checkbox" checked={allowRedirects} onChange={(event) => setAllowRedirects(event.target.checked)} />仅允许同源 HTTP 重定向</label>
          <div className="mcp-field-pair"><label>鉴权 Header<input value={secretHeader} onChange={(event) => setSecretHeader(event.target.value)} /></label><label>宿主环境变量<input value={secretEnv} onChange={(event) => setSecretEnv(event.target.value)} placeholder="MCP_API_TOKEN" /></label></div>
          <small>密钥值不会写入 mcp.json；这里只保存 env:VARIABLE 引用。</small>
        </>}
      </div> : null}
      {step === 3 ? <div className="mcp-test-panel"><h3>测试 initialize → tools/list</h3><p>配置已暂存为禁用状态。测试不会把工具暴露给新的 Agent 回合。</p>{tools?.error ? <div className="inline-error">{tools.error.code}: {tools.error.message}</div> : null}<button disabled={busy} onClick={() => void testConnection()}>{busy ? "正在测试…" : "开始连接测试"}</button></div> : null}
      {step === 4 ? <div className="mcp-tools-select"><div><h3>选择允许暴露给 Agent 的工具</h3><span>{tools?.protocol_version ? `MCP ${tools.protocol_version}` : "协议版本未知"}</span></div>{tools?.tools.map((tool) => <label key={tool.name}><input type="checkbox" checked={selected.has(tool.name)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(tool.name); else next.delete(tool.name); return next; })} /><span><strong>{tool.name}</strong><small>{tool.description || "无描述"}</small><details><summary>输入 Schema</summary><pre>{JSON.stringify(tool.input_schema ?? {}, null, 2)}</pre></details></span></label>)}<label className="mcp-inline-check"><input type="checkbox" checked={enableOnSave} onChange={(event) => setEnableOnSave(event.target.checked)} />保存后启用此 MCP 服务</label></div> : null}
      {error ? <div className="inline-error" role="alert">{error}</div> : null}
      <footer><button className="secondary-button" disabled={busy} onClick={step === 1 ? onClose : () => setStep((value) => Math.max(1, value - 1))}>{step === 1 ? "取消" : "上一步"}</button>{step === 1 ? <button onClick={() => setStep(2)}>下一步</button> : null}{step === 2 ? <button disabled={busy} onClick={() => void continueConnection()}>{busy ? "正在校验…" : "保存连接并继续"}</button> : null}{step === 4 ? <button disabled={busy || !selected.size} onClick={() => void finish()}>{busy ? "正在保存…" : "保存 MCP 服务"}</button> : null}</footer>
    </section>
  </div>;
}
