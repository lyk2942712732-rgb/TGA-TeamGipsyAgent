export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

export type StatusDefinition = {
  label: string;
  tone: StatusTone;
  description?: string;
};

export const STATUS_DICTIONARY: Record<string, StatusDefinition> = {
  created: { label: "已创建", tone: "neutral" },
  queued: { label: "排队中", tone: "info" },
  ready: { label: "可运行", tone: "info" },
  assigned: { label: "已分配", tone: "info" },
  running: { label: "运行中", tone: "info" },
  waiting: { label: "等待中", tone: "neutral" },
  awaiting_approval: { label: "等待审批", tone: "warning" },
  awaiting_user_input: { label: "等待用户输入", tone: "warning" },
  paused: { label: "已暂停", tone: "warning" },
  blocked: { label: "已阻塞", tone: "danger" },
  completed: { label: "已完成", tone: "success" },
  failed: { label: "失败", tone: "danger" },
  cancelled: { label: "已取消", tone: "danger" },
  archived: { label: "已归档", tone: "neutral" },
  pending: { label: "待处理", tone: "warning" },
  approved: { label: "已批准", tone: "success" },
  rejected: { label: "已拒绝", tone: "danger" },
  expired: { label: "已过期", tone: "neutral" },
  candidate: { label: "候选", tone: "warning" },
  confirmed: { label: "已确认", tone: "success" },
  verified: { label: "已验证", tone: "success" },
  superseded: { label: "已取代", tone: "neutral" },
  conflict: { label: "存在冲突", tone: "danger" },
  healthy: { label: "健康", tone: "success" },
  available: { label: "可用", tone: "success" },
  unavailable: { label: "不可用", tone: "danger" },
  loading: { label: "加载中", tone: "info" },
};

export function statusDefinition(value: string): StatusDefinition {
  return STATUS_DICTIONARY[value] ?? { label: value, tone: "neutral" };
}

export function statusLabel(value: string): string {
  return statusDefinition(value).label;
}

export const RISK_DICTIONARY: Record<string, StatusDefinition> = {
  passive: { label: "被动观察", tone: "success" },
  low: { label: "低风险", tone: "success" },
  medium: { label: "中风险", tone: "warning" },
  active: { label: "主动交互", tone: "warning" },
  high: { label: "高风险", tone: "danger" },
  destructive: { label: "破坏性", tone: "danger" },
  critical: { label: "严重风险", tone: "danger" },
};

export function riskDefinition(value: string): StatusDefinition {
  return RISK_DICTIONARY[value] ?? { label: value, tone: "neutral" };
}
