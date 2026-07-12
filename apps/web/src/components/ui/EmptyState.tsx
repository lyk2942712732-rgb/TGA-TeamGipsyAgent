export function EmptyState({ label }: { label: string }) { return <div className="empty-state">{label}</div>; }

export function statusLabel(status: string) { return ({ running: "运行中", paused: "已暂停", blocked: "被阻止", completed: "已完成", failed: "失败", cancelled: "已取消", created: "已创建" } as Record<string, string>)[status] ?? status; }
