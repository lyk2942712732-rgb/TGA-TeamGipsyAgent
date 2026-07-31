import { useEffect, useState } from "react";
import { Activity, Bot, RefreshCw, ShieldCheck } from "lucide-react";
import { BACKEND_CAPABILITIES } from "../api/capability-state";
import { fetchSystemHealth, type SystemComponent } from "../api/catalog-query-adapter";
import { CapabilityNotice, Chip, DisabledAction, ProductEmpty, ProductPageHeader, ProductTable, ProductTabs } from "../components/ui/ProductPrimitives";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { verifyLLMSettings } from "../api/tasks";

const TABS = ["核心组件", "执行环境", "存储与索引", "事件流", "最近告警"];

export function SystemPage() {
  const [items, setItems] = useState<SystemComponent[]>([]); const [tab, setTab] = useState("核心组件");
  const [loading, setLoading] = useState(true); const [error, setError] = useState(""); const [message, setMessage] = useState("");
  const load = async () => { setLoading(true); setError(""); try { setItems((await fetchSystemHealth()).components); } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取系统状态"); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  const healthy = items.filter((item) => item.status === "healthy" || item.status === "available").length;
  return <section className="product-page system-status-page">
    <ProductPageHeader title="系统状态" description="查看核心组件健康、执行环境、存储索引和事件流可用性。" action={<button disabled={loading} onClick={() => void load()}><RefreshCw size={15} />刷新状态</button>} />
    <div className="system-metrics"><Metric icon={<Activity size={18} />} label="整体健康" value={items.length ? `${healthy}/${items.length}` : "-"} detail="仅统计可真实探测组件" tone={healthy === items.length ? "success" : "warning"} /><Metric icon={<Activity size={18} />} label="运行中任务" value="-" detail="Dashboard 聚合数据未在此重复请求" tone="info" /><Metric icon={<Bot size={18} />} label="活跃 Solver" value="-" detail="Dashboard 聚合数据未在此重复请求" tone="info" /><Metric icon={<ShieldCheck size={18} />} label="待审批" value="-" detail="Dashboard 聚合数据未在此重复请求" tone="warning" /></div>
    <ProductTabs items={TABS} active={tab} onChange={setTab} />
    {error ? <ErrorState title="系统状态加载失败" description={error} actionLabel="重试" onAction={() => void load()} /> : null}
    {loading ? <LoadingSkeleton label="正在探测系统组件" rows={7} /> : null}
    {!loading && tab === "核心组件" ? <div className="system-status-layout"><div>{items.length ? <ProductTable label="核心组件表格" headers={["组件", "状态", "版本", "延迟", "最近成功", "最近错误", "详情"]}>{items.map((item) => <tr key={item.id}><td><strong>{item.label}</strong></td><td><Chip tone={tone(item.status)}>{statusLabel(item.status)}</Chip></td><td>{item.version || "-"}</td><td>{item.latencyMs == null ? "-" : `${item.latencyMs} ms`}</td><td>{date(item.lastSuccess)}</td><td>{item.lastError || "-"}</td><td><button className="link-button" title={item.detail}>查看</button></td></tr>)}</ProductTable> : <ProductEmpty title="暂无诊断数据" description="系统探测没有返回组件状态。" />}</div><aside className="system-actions"><section><h2>快速操作</h2><button onClick={() => void load()}><RefreshCw size={14} />刷新状态</button><button onClick={() => void verifyLLMSettings().then(() => setMessage("模型验证完成")).catch((reason) => setMessage(reason instanceof Error ? reason.message : "模型验证失败"))}>验证模型</button><DisabledAction reason="MCP 刷新需要选择具体服务">刷新 MCP</DisabledAction><DisabledAction reason="尚未提供全局索引诊断接口">检查索引</DisabledAction><DisabledAction reason="尚未提供统一诊断包接口">查看诊断</DisabledAction>{message ? <p role="status">{message}</p> : null}</section><section><h2>资源使用</h2><CapabilityNotice state={BACKEND_CAPABILITIES.systemResources.state} reason={BACKEND_CAPABILITIES.systemResources.reason} /><Resource label="CPU" /><Resource label="内存" /><Resource label="磁盘" /></section></aside></div> : null}
    {!loading && tab !== "核心组件" ? <section className="unsupported-workspace"><CapabilityNotice state="unsupported" reason={`${tab} 的全局诊断接口尚未提供。`} /><ProductEmpty title={`暂无${tab}数据`} description="未把静态演示数据作为真实系统状态。" /></section> : null}
  </section>;
}

function Metric({ icon, label, value, detail, tone }: { icon: React.ReactNode; label: string; value: string; detail: string; tone: string }) { return <article className={`system-metric ${tone}`}><div>{icon}<span>{label}</span></div><strong>{value}</strong><small>{detail}</small></article>; }
function Resource({ label }: { label: string }) { return <div className="system-resource"><span>{label}</span><b>不可探测</b><i /></div>; }
function tone(status: SystemComponent["status"]): "success" | "warning" | "danger" | "neutral" { return status === "healthy" || status === "available" ? "success" : status === "degraded" ? "warning" : status === "unsupported" ? "neutral" : "danger"; }
function statusLabel(status: SystemComponent["status"]) { return ({ healthy: "健康", available: "可用", degraded: "降级", unavailable: "不可用", unsupported: "不支持探测" } as const)[status]; }
function date(value: string | null) { return value ? new Date(value).toLocaleString("zh-CN") : "-"; }
