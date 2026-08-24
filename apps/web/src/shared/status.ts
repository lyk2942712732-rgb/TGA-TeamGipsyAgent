export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

export type StatusDefinition = {
  label: string;
  tone: StatusTone;
  description?: string;
};

export const STATUS_DICTIONARY: Record<string, StatusDefinition> = {
  created: { label: "已创建", tone: "neutral" },
  starting: { label: "启动中", tone: "info" },
  running: { label: "运行中", tone: "info" },
  waiting_user: { label: "等待用户", tone: "warning" },
  finalizing: { label: "最终确认中", tone: "info" },
  reporting: { label: "生成报告中", tone: "info" },
  idle: { label: "空闲", tone: "neutral" },
  pause_requested: { label: "请求暂停", tone: "warning" },
  stopped: { label: "已停止", tone: "neutral" },
  paused: { label: "已暂停", tone: "warning" },
  stopping: { label: "停止中", tone: "neutral" },
  completed: { label: "已完成", tone: "success" },
  failed: { label: "失败", tone: "danger" },
  cancelled: { label: "已取消", tone: "danger" },
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
