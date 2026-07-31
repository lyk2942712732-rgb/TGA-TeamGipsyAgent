import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, ExternalLink, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  decideGlobalApproval,
  fetchGlobalApprovals,
  type ApprovalQuery,
  type ApprovalStatus,
  type GlobalApproval,
} from "../api/operations-query-adapter";
import { ApprovalCard } from "../components/ui/ApprovalCard";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { EntityDrawer } from "../components/ui/EntityDrawer";
import { ErrorState } from "../components/ui/ErrorState";
import { FilterBar } from "../components/ui/FilterBar";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { PageHeader } from "../components/ui/PageHeader";
import { RiskBadge } from "../components/ui/RiskBadge";
import { StatusBadge } from "../shared/StatusBadge";

const STATUSES: Array<{ id: ApprovalStatus; label: string }> = [
  { id: "pending", label: "待处理" },
  { id: "approved", label: "已批准" },
  { id: "rejected", label: "已拒绝" },
  { id: "expired", label: "已过期" },
];
const PAGE_SIZE = 12;

export function ApprovalsPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<GlobalApproval | null>(null);
  const [decision, setDecision] = useState<"approve" | "reject" | null>(null);
  const [message, setMessage] = useState("");
  const query = useMemo<ApprovalQuery>(() => ({
    status: validStatus(params.get("status")),
    taskId: params.get("task_id") || undefined,
    solverId: params.get("solver_id") || undefined,
    intentId: params.get("intent_id") || undefined,
    risk: params.get("risk") || undefined,
    capability: params.get("capability") || undefined,
    deadline: params.get("deadline") || undefined,
    page: Math.max(1, Number(params.get("page") || 1) || 1),
    limit: PAGE_SIZE,
  }), [params]);
  useEffect(() => {
    if (params.has("status") && params.has("page")) return;
    const next = new URLSearchParams(params);
    if (!next.has("status")) next.set("status", query.status);
    if (!next.has("page")) next.set("page", String(query.page));
    setParams(next, { replace: true });
  }, [params, query.page, query.status, setParams]);
  const approvals = useQuery({
    queryKey: ["global-approvals", query],
    queryFn: () => fetchGlobalApprovals(query),
  });
  const mutation = useMutation({
    mutationFn: ({ item, value }: { item: GlobalApproval; value: "approve" | "reject" }) => decideGlobalApproval(item, value),
    onSuccess: async (_value, variables) => {
      setMessage(variables.value === "approve" ? "已提交一次性批准" : "已拒绝该操作");
      setDecision(null);
      setSelected(null);
      await client.invalidateQueries({ queryKey: ["global-approvals"] });
      await client.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const update = (key: string, value?: string, resetPage = true) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (resetPage) next.delete("page");
    setParams(next, { replace: true });
  };
  const clear = () => setParams({ status: query.status, page: "1" }, { replace: true });
  const item = decision ? selected : null;

  return <section className="page-stack approvals-page">
    <PageHeader
      eyebrow="GOVERNANCE / APPROVALS"
      title="全局审批中心"
      description="审查受控 Action 的影响与替代方案；决策仍由任务级审批状态机执行。"
      breadcrumbs={[{ label: "TGA", href: "/" }, { label: "审批中心" }]}
    />
    <div className="approval-tabs" role="tablist" aria-label="审批状态">{STATUSES.map((tab) => <button
      key={tab.id}
      type="button"
      role="tab"
      aria-selected={query.status === tab.id}
      className={query.status === tab.id ? "active" : ""}
      onClick={() => update("status", tab.id)}
    >{tab.label}</button>)}</div>

    <FilterBar resultCount={approvals.data?.total} actions={<button className="secondary-button" onClick={clear}><RotateCcw size={13} />重置</button>}>
      <label>Task<input value={query.taskId ?? ""} placeholder="Task ID" onChange={(event) => update("task_id", event.target.value)} /></label>
      <label>Solver<input value={query.solverId ?? ""} placeholder="Solver ID" onChange={(event) => update("solver_id", event.target.value)} /></label>
      <label>Intent<input value={query.intentId ?? ""} placeholder="Intent ID" onChange={(event) => update("intent_id", event.target.value)} /></label>
      <label>Risk<select value={query.risk ?? ""} onChange={(event) => update("risk", event.target.value)}><option value="">全部</option><option value="passive">被动</option><option value="active">主动</option><option value="destructive">破坏性</option></select></label>
      <label>Capability<input value={query.capability ?? ""} placeholder="精确 Capability" onChange={(event) => update("capability", event.target.value)} /></label>
      <label>截止时间<select value={query.deadline ?? ""} onChange={(event) => update("deadline", event.target.value)}><option value="">全部</option><option value="overdue">已超时</option><option value="24h">24 小时内</option><option value="7d">7 天内</option><option value="none">无截止时间</option></select></label>
    </FilterBar>

    {message ? <p className="approval-feedback" role="status">{message}</p> : null}
    {mutation.isError ? <p className="inline-error" role="alert">{mutation.error instanceof Error ? mutation.error.message : "审批操作失败"}</p> : null}
    {approvals.isLoading ? <LoadingSkeleton label="正在读取审批队列" rows={8} /> : null}
    {approvals.isError ? <ErrorState title="审批队列加载失败" description={approvals.error instanceof Error ? approvals.error.message : "无法读取审批聚合"} actionLabel="重试" onAction={() => void approvals.refetch()} /> : null}
    {approvals.data && !approvals.data.items.length ? <EmptyState label={`当前筛选下没有${STATUSES.find((tab) => tab.id === query.status)?.label ?? ""}审批。`} /> : null}
    {approvals.data?.items.length ? <div className="global-approval-grid">{approvals.data.items.map((approval) => <ApprovalCard
      key={approval.approval_id}
      title={approval.capability}
      status={approval.status}
      risk={approval.risk}
      details={<ApprovalSummary item={approval} />}
      rationale={<><b>Rationale</b>{approval.rationale || "未提供"}</>}
      actions={<>
        <button className="text-button" onClick={() => setSelected(approval)}>查看详情</button>
        <button className="secondary-button" onClick={() => navigate(`/tasks/${encodeURIComponent(approval.task_id)}`)}>任务上下文 <ExternalLink size={12} /></button>
        {query.status === "pending" ? <><button className="danger-button" disabled={!approval.decision_allowed || mutation.isPending} title={approval.decision_block_reason ?? "拒绝该操作"} onClick={() => { setSelected(approval); setDecision("reject"); }}>拒绝</button><button disabled={!approval.decision_allowed || mutation.isPending} title={approval.decision_block_reason ?? "仅批准本次"} onClick={() => { setSelected(approval); setDecision("approve"); }}>批准本次</button></> : null}
      </>}
    />)}</div> : null}

    {approvals.data ? <footer className="approval-pagination"><span>第 {query.page} 页 · 共 {approvals.data.total} 项</span><div><button className="secondary-button" aria-label="上一页" disabled={(query.page ?? 1) <= 1} onClick={() => update("page", String((query.page ?? 1) - 1), false)}><ChevronLeft size={14} /></button><button className="secondary-button" aria-label="下一页" disabled={!approvals.data.next_offset} onClick={() => update("page", String((query.page ?? 1) + 1), false)}><ChevronRight size={14} /></button></div></footer> : null}

    <EntityDrawer
      open={Boolean(selected) && !decision}
      title={selected?.capability ?? "审批详情"}
      description={selected ? `${selected.task_name} · ${selected.action_id}` : undefined}
      onClose={() => setSelected(null)}
      footer={selected ? <button className="secondary-button" onClick={() => navigate(`/tasks/${encodeURIComponent(selected.task_id)}`)}>打开任务上下文</button> : undefined}
    >{selected ? <ApprovalDetail item={selected} /> : null}</EntityDrawer>

    <ConfirmDialog
      open={Boolean(item && decision)}
      title={decision === "approve" ? "批准本次操作？" : "拒绝该操作？"}
      description={decision === "approve" ? "仅批准当前 Action ID，不修改全局策略或任务授权范围。" : "拒绝后任务级状态机会记录结果并恢复相应执行上下文。"}
      confirmLabel={decision === "approve" ? "批准本次" : "确认拒绝"}
      danger={decision === "reject"}
      busy={mutation.isPending}
      details={item ? <div className="approval-confirm-summary"><b>{item.capability}</b><span>{item.target}</span><RiskBadge value={item.risk} /></div> : null}
      onCancel={() => setDecision(null)}
      onConfirm={() => { if (item && decision) mutation.mutate({ item, value: decision }); }}
    />
  </section>;
}

function ApprovalSummary({ item }: { item: GlobalApproval }) {
  return <dl className="approval-summary"><Row label="Task" value={item.task_name} /><Row label="Solver" value={item.solver_id || "未投影"} /><Row label="Intent" value={item.intent_id || "Task scope"} /><Row label="Target" value={item.target} /><Row label="Expires" value={formatDate(item.expires_at)} /></dl>;
}

function ApprovalDetail({ item }: { item: GlobalApproval }) {
  return <div className="approval-detail"><div className="approval-detail-badges"><StatusBadge value={item.status} /><RiskBadge value={item.risk} /></div><dl>
    <Row label="Task" value={`${item.task_name} (${item.task_id})`} />
    <Row label="Solver" value={item.solver_id || "未投影"} />
    <Row label="Intent" value={item.intent_id || "Task scope"} />
    <Row label="Action" value={`${item.action_kind} · ${item.action_id}`} />
    <Row label="Capability" value={item.capability} />
    <Row label="Target" value={item.target} />
    <Row label="Effect" value={String(item.effect.description ?? "未提供")} />
    <Row label="Rationale" value={item.rationale || "未提供"} />
    <Row label="Expected Outcome" value={item.expected_outcome || "未提供"} />
    <Row label="Alternative Analysis" value={item.alternative_analysis || item.alternatives.join("；") || "未提供"} />
    <Row label="Reversibility" value={item.reversibility} />
    <Row label="Expires At" value={formatDate(item.expires_at)} />
  </dl>{item.decision_block_reason ? <p className="approval-block-reason">{item.decision_block_reason}</p> : null}</div>;
}

function Row({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
function validStatus(value: string | null): ApprovalStatus { return STATUSES.some((item) => item.id === value) ? value as ApprovalStatus : "pending"; }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString("zh-CN") : "无截止时间"; }
