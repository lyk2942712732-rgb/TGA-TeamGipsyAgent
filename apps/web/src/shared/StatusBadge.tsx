export function StatusBadge({ value }: { value: string }) {
  return <span className={`runtime-status status-${value}`}><i aria-hidden="true" />{statusLabel(value)}</span>;
}

function statusLabel(value: string): string {
  return ({ created: "已创建", queued: "排队中", ready: "可运行", running: "运行中", waiting: "等待中", paused: "已暂停", awaiting_approval: "等待审批", assigned: "已分配", completed: "已完成", failed: "失败", cancelled: "已取消", pending: "待处理", approved: "已批准", rejected: "已拒绝", confirmed: "已确认", candidate: "候选" } as Record<string, string>)[value] ?? value;
}
