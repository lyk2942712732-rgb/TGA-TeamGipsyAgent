import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CalendarDays } from "lucide-react";
import {
  decideGlobalApproval,
  fetchGlobalApprovals,
  type ApprovalQuery,
  type ApprovalStatus,
  type GlobalApproval,
} from "../api/operations-query-adapter";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { Pagination } from "../components/ui/CatalogTable";
import { approvalMeta, type ApprovalView } from "./approvals-view";

const STATUSES: Array<{ id: ApprovalStatus; label: string }> = [
  { id: "pending", label: "待处理" },
  { id: "approved", label: "已批准" },
  { id: "rejected", label: "已拒绝" },
  { id: "expired", label: "已过期" },
];
const PAGE_SIZE = 12;

export function ApprovalsPage() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [pending, setPending] = useState<{ approval: ApprovalView; decision: "approve" | "reject" } | null>(null);
  const [message, setMessage] = useState("");

  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const query = useMemo<ApprovalQuery>(() => ({
    status: validStatus(params.get("status")),
    taskId: params.get("task_id") || undefined,
    solverId: params.get("solver_id") || undefined,
    risk: params.get("risk") || undefined,
    capability: params.get("capability") || undefined,
    deadline: params.get("deadline") || undefined,
    page,
    limit: PAGE_SIZE,
  }), [params, page]);

  const approvals = useQuery({ queryKey: ["approvals", query], queryFn: () => fetchGlobalApprovals(query) });
  const items = approvals.data?.items ?? [];
  // Filter options are drawn from the current page so a select never offers a
  // task or capability that is not actually in the queue.
  const filterItems = items;
  const total = approvals.data?.total ?? 0;

  const decide = useMutation({
    mutationFn: ({ approval, decision }: { approval: GlobalApproval; decision: "approve" | "reject" }) =>
      decideGlobalApproval(approval, decision),
    onSuccess: async (_result, variables) => {
      setMessage(variables.decision === "approve" ? "已提交一次性批准" : "已提交拒绝决定");
      setPending(null);
      await client.invalidateQueries({ queryKey: ["approvals"] });
      await client.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (error) => {
      setMessage(error instanceof Error ? error.message : "审批提交失败");
      setPending(null);
    },
  });

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next);
  };

  // Only the pending tab knows its own count; the others would need a separate
  // aggregate query, so they stay unlabelled rather than showing a wrong number.
  const pendingTotal = query.status === "pending" ? total : null;
  const tabs: DetailTab[] = STATUSES.map((status) => ({
    id: status.id,
    label: status.id === "pending" && pendingTotal !== null
      ? `${status.label}（${pendingTotal}）`
      : status.label,
  }));

  const confirmDecision = () => {
    if (!pending) return;
    decide.mutate(pending);
  };

  return <div className="ref-page approval-page">
    <header className="ref-page-head">
      <div>
        <h1>审批中心</h1>
        <p>需要您审批的高风险操作</p>
      </div>
    </header>

    <DetailTabs tabs={tabs} active={query.status} onSelect={(id) => update("status", id)} size="lg" />

    <section className="ref-filter-row" aria-label="筛选审批">
      <select aria-label="任务筛选" value={params.get("task_id") ?? ""} onChange={(event) => update("task_id", event.target.value)}>
        <option value="">所有任务</option>
        {[...new Set(filterItems.map((item) => item.task_id))].map((value) => (
          <option key={value} value={value}>{filterItems.find((item) => item.task_id === value)?.task_name || value}</option>
        ))}
      </select>
      <select aria-label="风险筛选" value={params.get("risk") ?? ""} onChange={(event) => update("risk", event.target.value)}>
        <option value="">所有风险</option>
        <option value="passive">被动观察</option>
        <option value="active">主动交互</option>
        <option value="destructive">破坏性</option>
      </select>
      <select aria-label="Solver 筛选" value={params.get("solver_id") ?? ""} onChange={(event) => update("solver_id", event.target.value)}>
        <option value="">所有 Solver</option>
        {[...new Set(filterItems.map((item) => item.solver_id))].map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
      <select aria-label="Capability 筛选" value={params.get("capability") ?? ""} onChange={(event) => update("capability", event.target.value)}>
        <option value="">Capability</option>
        {[...new Set(filterItems.map((item) => item.capability))].map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
      <label className="approval-date-filter">
        <span>{params.get("deadline") || "截止时间"}</span>
        <CalendarDays size={14} aria-hidden="true" />
        <input
          type="date"
          aria-label="截止时间筛选"
          value={params.get("deadline") ?? ""}
          onChange={(event) => update("deadline", event.target.value)}
        />
      </label>
    </section>

    {message ? <p className="settings-message" role="status">{message}</p> : null}

    {approvals.isLoading ? <LoadingSkeleton label="正在读取审批队列" rows={5} />
      : approvals.isError ? <ErrorState
        description={approvals.error instanceof Error ? approvals.error.message : "无法读取审批队列"}
        actionLabel="重试"
        onAction={() => void approvals.refetch()}
      />
      : !items.length ? <EmptyState
        title="当前筛选下没有待处理审批。"
        description="Worker 提交高风险操作后，会在这里等待你的裁决。"
      />
      : <>
        <div className="approval-list ref-fill">
          {items.map((item) => <ApprovalRecord
            key={item.approval_id}
            approval={item}
            busy={decide.isPending}
            onDecide={(decision) => setPending({ approval: item, decision })}
          />)}
        </div>
        {total > PAGE_SIZE ? <Pagination
          total={total}
          pageSize={PAGE_SIZE}
          page={page}
          onPage={(value) => update("page", String(value))}
        /> : null}
      </>}

    <ConfirmDialog
      open={pending !== null}
      title={pending?.decision === "approve" ? "批准本次操作？" : "拒绝该操作？"}
      description={pending
        ? `${pending.approval.capability} → ${pending.approval.target}。该决定会立即写入治理审计记录。`
        : ""}
      confirmLabel={pending?.decision === "approve" ? "批准一次" : "确认拒绝"}
      danger={pending?.decision === "reject"}
      busy={decide.isPending}
      onConfirm={confirmDecision}
      onCancel={() => setPending(null)}
    />
  </div>;
}

function ApprovalRecord({ approval, busy, onDecide }: {
  approval: ApprovalView;
  busy: boolean;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const meta = approvalMeta(approval);
  return <article className="approval-record">
    <header>
      <div className="approval-title">
        <h2>{meta.title}</h2>
        <span className={`approval-risk-chip ${meta.riskLabel === "高风险" ? "tone-danger" : "tone-warning"}`}>{meta.riskLabel}</span>
        <span className="approval-category-chip">{meta.categoryLabel}</span>
      </div>
    </header>

    <div className="approval-columns">
      <dl className="field-grid">
        <Row label="任务" value={approval.task_name || approval.task_id} />
        <Row label="Solver" value={approval.solver_id} />
        <Row label="目标" value={<code className="cell-mono">{approval.target}</code>} />
        <Row label="原因" value={approval.rationale} />
        <Row label="影响范围" value={effectSummary(approval.effect) || approval.expected_outcome || "—"} />
      </dl>
      <dl className="field-grid">
        <Row label="可逆性" value={REVERSIBILITY_LABELS[approval.reversibility] ?? approval.reversibility} />
        <Row label="替代方案" value={approval.alternative_analysis || approval.alternatives.join("；") || "—"} />
        <Row label="请求时间" value={formatDate(approval.created_at)} />
        <Row label="截止时间" value={meta.deadlineLabel ?? (approval.expires_at ? formatDate(approval.expires_at) : "—")} />
      </dl>
    </div>

    {approval.status === "pending" ? <footer className="approval-actions">
      {approval.decision_allowed ? null
        : <p className="approval-blocked">{approval.decision_block_reason ?? "当前不可裁决"}</p>}
      <button
        className="ref-secondary-button"
        disabled={busy || !approval.decision_allowed}
        onClick={() => onDecide("reject")}
      >拒绝</button>
      <button
        className="ref-primary-button"
        disabled={busy || !approval.decision_allowed}
        onClick={() => onDecide("approve")}
      >批准一次</button>
    </footer> : null}
  </article>;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="field-grid-row"><dt>{label}</dt><dd>{value}</dd></div>;
}

const REVERSIBILITY_LABELS: Record<string, string> = {
  reversible: "可逆", irreversible: "不可逆", uncertain: "不确定", not_applicable: "不适用",
};

const EFFECT_SCOPES: Record<string, string> = {
  none: "无副作用", session: "本次会话", workspace: "任务工作区", target: "目标系统",
};

/** ActionEffect is the reviewable side-effect declaration attached to an Action. */
function effectSummary(effect: Record<string, unknown>): string {
  const scope = EFFECT_SCOPES[String(effect?.scope ?? "")] ?? "";
  const description = typeof effect?.description === "string" ? effect.description : "";
  return [scope, description].filter(Boolean).join("：");
}

function validStatus(value: string | null): ApprovalStatus {
  return STATUSES.some((status) => status.id === value) ? value as ApprovalStatus : "pending";
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value
    : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
