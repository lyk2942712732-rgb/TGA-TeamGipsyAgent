/** One Intent card as the Kanban board renders it. */
export type IntentCardView = {
  key: string;
  intentId: string;
  title: string;
  objective: string;
  status: string;
  priority: string;
  solver: string | null;
  /** Column-specific detail lines — 预计 / 运行时长 / 耗时 / 阻塞原因 … */
  metrics: Array<[string, string]>;
  percent: number | null;
  flag: "approval" | "blocked" | "done" | null;
};
