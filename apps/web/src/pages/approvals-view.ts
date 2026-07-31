import type { ApprovalQuery, ApprovalStatus, GlobalApproval } from "../api/operations-query-adapter";

export type ApprovalDisplayMeta = {
  title: string;
  riskLabel: string;
  categoryLabel: string;
  deadlineLabel?: string;
};

export type ApprovalView = GlobalApproval & {
  sample?: boolean;
  display?: ApprovalDisplayMeta;
};

export const SAMPLE_APPROVALS: readonly ApprovalView[] = [
  {
    approval_id: "sample-approval-server-file",
    task_id: "sample-web-api-security-test",
    task_name: "Web API 安全测试",
    solver_id: "Web Analyst",
    intent_id: "sample-log-review",
    action_id: "sample-action-server-file",
    action_kind: "filesystem",
    capability: "filesystem.read",
    target: "server-01:/var/log/nginx/access.log",
    risk: "destructive",
    effect: { description: "网站的 2GB 硬盘空间" },
    rationale: "调取日志文件以获取访问日志详情",
    expected_outcome: "读取服务器访问日志",
    alternative_analysis: "查看日志汇总报告",
    alternatives: ["查看日志汇总报告"],
    reversibility: "irreversible",
    expires_at: "2024-05-20T12:21:33+08:00",
    status: "pending",
    decision_allowed: true,
    decision_block_reason: null,
    created_at: "2024-05-20T10:21:33+08:00",
    updated_at: "2024-05-20T10:21:33+08:00",
    sample: true,
    display: { title: "审批服务器文件", riskLabel: "高风险", categoryLabel: "破坏性", deadlineLabel: "2小时后" },
  },
  {
    approval_id: "sample-approval-sql-injection",
    task_id: "sample-intranet-penetration",
    task_name: "内网渗透评估",
    solver_id: "Web Analyst",
    intent_id: "sample-sql-verification",
    action_id: "sample-action-sql-injection",
    action_kind: "network",
    capability: "network.request",
    target: "10.0.0.1:8080/api/user",
    risk: "destructive",
    effect: { description: "内网应用服务" },
    rationale: "验证 SQL 注入漏洞",
    expected_outcome: "确认 SQL 注入漏洞可利用性",
    alternative_analysis: "查看现有 WAF 日志",
    alternatives: ["查看现有 WAF 日志"],
    reversibility: "reversible",
    expires_at: "2024-05-20T10:45:12+08:00",
    status: "pending",
    decision_allowed: true,
    decision_block_reason: null,
    created_at: "2024-05-20T09:45:12+08:00",
    updated_at: "2024-05-20T09:45:12+08:00",
    sample: true,
    display: { title: "发起 SQL 注入测试", riskLabel: "高风险", categoryLabel: "网络攻击", deadlineLabel: "1小时后" },
  },
  {
    approval_id: "sample-approval-upload-file",
    task_id: "sample-reverse-analysis",
    task_name: "样本逆向分析",
    solver_id: "Code Audit",
    intent_id: "sample-poc-upload",
    action_id: "sample-action-upload-file",
    action_kind: "filesystem",
    capability: "filesystem.write",
    target: "172.16.0.5 / upload",
    risk: "active",
    effect: { description: "目标服务器 / 临时目录" },
    rationale: "上传 PoC 文件验证漏洞",
    expected_outcome: "完成 PoC 验证",
    alternative_analysis: "使用 SFTP 临时目录",
    alternatives: ["使用 SFTP 临时目录"],
    reversibility: "reversible",
    expires_at: "2024-05-20T09:25:21+08:00",
    status: "pending",
    decision_allowed: true,
    decision_block_reason: null,
    created_at: "2024-05-20T08:55:21+08:00",
    updated_at: "2024-05-20T08:55:21+08:00",
    sample: true,
    display: { title: "上传测试文件", riskLabel: "中风险", categoryLabel: "权限变更", deadlineLabel: "30分钟后" },
  },
];

export function approvalViewItems(realItems: readonly GlobalApproval[], query: ApprovalQuery, sampleStatuses: Record<string, ApprovalStatus>): ApprovalView[] {
  const real = realItems as readonly ApprovalView[];
  const sample = SAMPLE_APPROVALS
    .map((item) => ({ ...item, status: sampleStatuses[item.approval_id] ?? item.status }))
    .filter((item) => matchesQuery(item, query));
  const fillerCount = Math.max(0, 3 - real.length);
  return [...real, ...sample.slice(0, fillerCount)];
}

export function approvalFilterItems(realItems: readonly GlobalApproval[], sampleStatuses: Record<string, ApprovalStatus>): ApprovalView[] {
  return [
    ...(realItems as readonly ApprovalView[]),
    ...SAMPLE_APPROVALS.map((item) => ({ ...item, status: sampleStatuses[item.approval_id] ?? item.status })),
  ];
}

export function samplePendingCount(sampleStatuses: Record<string, ApprovalStatus>): number {
  return SAMPLE_APPROVALS.filter((item) => (sampleStatuses[item.approval_id] ?? item.status) === "pending").length;
}

export function approvalMeta(approval: ApprovalView): ApprovalDisplayMeta {
  if (approval.display) return approval.display;
  const riskLabel = approval.risk === "destructive" ? "高风险" : approval.risk === "active" ? "中风险" : "低风险";
  const categoryLabel = approval.reversibility === "irreversible"
    ? "破坏性"
    : approval.action_kind.includes("network") ? "网络攻击" : "主动操作";
  return { title: approval.capability, riskLabel, categoryLabel };
}

function matchesQuery(item: ApprovalView, query: ApprovalQuery): boolean {
  if (item.status !== query.status) return false;
  if (query.taskId && item.task_id !== query.taskId) return false;
  if (query.solverId && item.solver_id !== query.solverId) return false;
  if (query.risk && item.risk !== query.risk) return false;
  if (query.capability && item.capability !== query.capability) return false;
  if (query.deadline && !item.expires_at?.startsWith(query.deadline)) return false;
  return true;
}
