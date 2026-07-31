import { FormEvent, useEffect, useState } from "react";
import { Eye, EyeOff, Plus } from "lucide-react";
import { getLLMSettings, updateLLMSettings, verifyLLMSettings, type LLMSettings } from "../api/tasks";
import { CatalogTable, type Column } from "../components/ui/CatalogTable";
import { EmptyState } from "../components/ui/EmptyState";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { FieldGrid } from "../components/ui/FieldGrid";
import { StatusBadge } from "../shared/StatusBadge";

/**
 * Reference design shows a multi-provider console.  The backend exposes exactly
 * one OpenAI-compatible provider through `/api/v2/settings/llm`, so the table
 * renders that single real row and every other affordance is marked unbuilt.
 */

const TABS: DetailTab[] = [
  { id: "providers", label: "Providers" },
  { id: "profiles", label: "Model Profiles", missing: true },
  { id: "routing", label: "Role Routing", missing: true },
  { id: "history", label: "验证历史", missing: true },
];

type ProviderRow = {
  id: string;
  name: string;
  type: string;
  status: string;
  lastVerified: string | null;
  models: number | null;
};

type Draft = {
  base_url: string; model: string; api_key: string;
  supports_vision: boolean | null; max_output_tokens: number;
  timeout_seconds: number; temperature: number;
  reasoning_mode: "auto" | "enabled" | "disabled";
};

export function ModelsPage({ onConfiguredChange }: { onConfiguredChange?: (configured: boolean) => void }) {
  const [tab, setTab] = useState("providers");
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [message, setMessage] = useState("");
  const [draft, setDraft] = useState<Draft>({
    base_url: "", model: "", api_key: "", supports_vision: null,
    max_output_tokens: 1024, timeout_seconds: 60, temperature: 0.2, reasoning_mode: "auto",
  });

  useEffect(() => {
    void getLLMSettings().then((value) => {
      setSettings(value);
      setDraft({
        base_url: value.base_url, model: value.model, api_key: "",
        supports_vision: value.supports_vision ?? null,
        max_output_tokens: value.max_output_tokens ?? 1024,
        timeout_seconds: value.timeout_seconds ?? 60,
        temperature: value.temperature ?? 0.2,
        reasoning_mode: value.reasoning_mode ?? "auto",
      });
    });
  }, []);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setMessage("");
    try {
      const next = await updateLLMSettings({ ...draft, api_key: draft.api_key || undefined });
      setSettings(next);
      onConfiguredChange?.(next.configured);
      setDraft((current) => ({ ...current, api_key: "" }));
      setMessage("Provider、模型和凭据设置已保存。API Key 不会回显。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "保存失败");
    }
  };

  const verify = async () => {
    setVerifying(true);
    setMessage("");
    try {
      const result = await verifyLLMSettings();
      setMessage(result.reachable && result.action_tools
        ? `模型连接、强制/自动工具调用及产品工具目录验证成功：${result.model}（${result.tool_catalog.tool_count} 个工具）`
        : "模型未返回有效工具调用。");
      setSettings(await getLLMSettings());
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "模型工具调用协议验证失败");
      setSettings(await getLLMSettings());
    } finally {
      setVerifying(false);
    }
  };

  const requestUrl = normalizeUrl(draft.base_url);
  const rows: ProviderRow[] = settings ? [{
    id: "openai-compatible",
    name: providerName(settings.base_url),
    type: isLocal(settings.base_url) ? "本地" : "云端",
    status: providerStatus(settings),
    lastVerified: settings.verification?.verified_at ?? null,
    models: settings.model ? 1 : null,
  }] : [];

  const columns: Array<Column<ProviderRow>> = [
    { id: "name", header: "Provider 名称", render: (row) => <strong>{row.name}</strong> },
    { id: "type", header: "类型", render: (row) => <span className="cell-muted">{row.type}</span> },
    {
      id: "status", header: "状态",
      render: (row) => <span className={`ref-chip ${row.status === "healthy" ? "tone-ok" : row.status === "degraded" ? "tone-warn" : "tone-danger"}`}>
        <i className="ref-dot" aria-hidden="true" />{row.status === "healthy" ? "正常" : row.status === "degraded" ? "警告" : "未配置"}
      </span>,
    },
    {
      id: "verified", header: "最近验证",
      render: (row) => row.lastVerified
        ? <span className="cell-muted">{new Date(row.lastVerified).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
        : <span className="field-empty">尚未验证</span>,
    },
    { id: "models", header: "可用模型数", render: (row) => row.models ?? <span className="field-empty">—</span>, align: "center" },
    {
      id: "actions", header: "操作",
      render: () => <button
        className="ref-secondary-button"
        onClick={(event) => {
          event.stopPropagation();
          document.getElementById("provider-settings")?.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
      >设置</button>,
    },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>Models 管理</h1>
        <p>管理模型提供商和模型配置</p>
      </div>
      <button className="ref-primary-button" onClick={() => setMessage("添加 Provider：该功能尚未开放，后端目前仅支持单个 OpenAI-compatible Provider。")}>
        <Plus size={16} />添加 Provider
      </button>
    </header>

    <DetailTabs tabs={TABS} active={tab} onSelect={setTab} size="lg" />

    {tab !== "providers"
      ? <EmptyState label={`暂无${TABS.find((item) => item.id === tab)?.label}数据`} />
      : <>
        <CatalogTable columns={columns} rows={rows} rowKey={(row) => row.id} label="Provider 列表" emptyLabel="正在读取 Provider 设置…" />

        <section className="ref-detail-panel ref-fill" id="provider-settings">
          <header className="ref-detail-head">
            <div className="ref-detail-title"><h2>{settings ? providerName(settings.base_url) : "Provider"}</h2></div>
            <StatusBadge value={providerStatus(settings)} label={verificationLabel(settings)} />
          </header>

          {/* The reference's read-only summary, then the working settings form
              below it — the save/verify path is real and must stay usable. */}
          <FieldGrid columns={2} fields={[
            { label: "类型", value: settings ? (isLocal(settings.base_url) ? "本地" : "云端") : null },
            { label: "凭据状态", value: settings?.api_key_set ? "有效" : "未设置" },
            { label: "API 端点", value: <code className="cell-mono">{settings?.base_url}</code> },
            { label: "速率限制", missing: true },
            { label: "组织 ID", missing: true },
            { label: "并发限制", missing: true },
            { label: "验证状态", value: verificationLabel(settings) },
            { label: "超时设置", value: settings?.timeout_seconds ? `${settings.timeout_seconds} 秒` : null },
            {
              label: "最后验证",
              value: settings?.verification?.verified_at
                ? new Date(settings.verification.verified_at).toLocaleString("zh-CN")
                : null,
              missing: !settings?.verification?.verified_at,
            },
            { label: "可用模型", value: settings?.model },
          ]} />

          <h3 className="ref-subhead">连接设置</h3>
          <form className="models-form" onSubmit={save}>
            <div className="models-form-grid">
              <label>Provider Base URL
                <input required type="url" autoComplete="url" placeholder="https://provider.example/v1"
                  value={draft.base_url} onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} />
              </label>
              <label>模型 ID
                <input required autoComplete="off" placeholder="provider-model-id"
                  value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} />
              </label>
              <div className="secret-field">
                <label htmlFor="llm-api-key">API Key</label>
                <div className="secret-input">
                  <input id="llm-api-key" type={showKey ? "text" : "password"} autoComplete="new-password"
                    placeholder={settings?.api_key_set ? "已保存，留空表示不修改" : "输入 Provider API Key"}
                    value={draft.api_key} onChange={(e) => setDraft({ ...draft, api_key: e.target.value })} />
                  <button type="button" className="icon-button"
                    aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}
                    onClick={() => setShowKey((value) => !value)}>
                    {showKey ? <EyeOff size={17} /> : <Eye size={17} />}
                  </button>
                </div>
              </div>
              <label>视觉输入
                <select value={draft.supports_vision == null ? "auto" : String(draft.supports_vision)}
                  onChange={(e) => setDraft({ ...draft, supports_vision: e.target.value === "auto" ? null : e.target.value === "true" })}>
                  <option value="auto">自动探测</option>
                  <option value="true">支持图像输入</option>
                  <option value="false">仅文本</option>
                </select>
              </label>
              <label>最大输出 Token
                <input type="number" min={256} max={16384} value={draft.max_output_tokens}
                  onChange={(e) => setDraft({ ...draft, max_output_tokens: Number(e.target.value) })} />
              </label>
              <label>请求超时（秒）
                <input type="number" min={5} max={300} value={draft.timeout_seconds}
                  onChange={(e) => setDraft({ ...draft, timeout_seconds: Number(e.target.value) })} />
              </label>
              <label>Temperature
                <input type="number" min={0} max={2} step={0.1} value={draft.temperature}
                  onChange={(e) => setDraft({ ...draft, temperature: Number(e.target.value) })} />
              </label>
              <label>推理模型模式
                <select value={draft.reasoning_mode}
                  onChange={(e) => setDraft({ ...draft, reasoning_mode: e.target.value as Draft["reasoning_mode"] })}>
                  <option value="auto">自动</option>
                  <option value="enabled">开启</option>
                  <option value="disabled">关闭</option>
                </select>
              </label>
            </div>

            <FieldGrid columns={2} fields={[
              { label: "最终请求地址", value: <code className="cell-mono">{requestUrl}</code> },
              { label: "工具协议", value: settings?.verification?.capabilities?.tool_calling ? "已验证" : "待验证" },
            ]} />

            {settings?.verification?.last_error
              ? <p className="inline-error" role="alert">{settings.verification.last_error.code}：{settings.verification.last_error.message}</p>
              : null}
            {message ? <p className="settings-message" role="status">{message}</p> : null}

            <div className="policy-actions">
              <button className="ref-primary-button">保存设置</button>
              <button type="button" className="ref-secondary-button" disabled={!settings?.configured || verifying}
                onClick={() => void verify()}>{verifying ? "正在验证…" : "验证模型连接"}</button>
            </div>
          </form>
        </section>
      </>}
  </div>;
}

/** The reference labels each row by vendor; derive it from the endpoint host. */
function providerName(baseUrl: string): string {
  try {
    const host = new URL(baseUrl).hostname;
    const parts = host.split(".").filter((part) => part !== "api" && part !== "www");
    const label = parts[0] ?? host;
    return label.charAt(0).toUpperCase() + label.slice(1);
  } catch {
    return baseUrl || "OpenAI-compatible Provider";
  }
}

function isLocal(baseUrl: string): boolean {
  return /localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0/.test(baseUrl);
}

function normalizeUrl(base: string): string {
  const trimmed = base.replace(/\/$/, "");
  return trimmed.endsWith("/chat/completions") ? trimmed : `${trimmed}/chat/completions`;
}

function providerStatus(settings: LLMSettings | null): string {
  if (!settings?.configured) return "unavailable";
  return settings.verification_status === "verified" ? "healthy" : "degraded";
}

function verificationLabel(settings: LLMSettings | null): string {
  switch (settings?.verification_status) {
    case "verified": return "验证通过";
    case "verifying": return "验证中";
    case "failed": return "验证失败";
    case "stale": return "配置已修改，需重新验证";
    default: return settings?.configured ? "已保存，待验证" : "未配置";
  }
}
