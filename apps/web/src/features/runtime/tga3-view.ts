import type { TGA3Agent, TGA3BoardEntry, TGA3DialogueMessage } from "../../runtime/tga3-runtime";

export const TASK_STATE_LABELS: Record<string, string> = {
  created: "已创建", starting: "启动中", running: "运行中", waiting_user: "等待用户", finalizing: "固定结论",
  reporting: "生成报告", completed: "已完成", failed: "失败", cancelled: "已取消", stopping: "停止中", stopped: "已停止",
};
export const AGENT_STATE_LABELS: Record<string, string> = {
  created: "已创建", starting: "启动中", running: "运行中", idle: "待命", pause_requested: "正在暂停",
  paused: "已暂停", stopping: "停止中", stopped: "已停止", completed: "已完成", failed: "失败",
};
export const ROLE_LABELS: Record<string, string> = { supervisor: "顾问", worker: "执行", reporter: "报告" };
export const BOARD_KIND_LABELS: Record<TGA3BoardEntry["kind"], string> = {
  user_prompt: "用户提示", user_file: "用户文件", supervisor_advice: "Supervisor 建议", intel: "Intel", finding: "Finding", qa: "Q&A", final_candidate: "最终候选",
};
export const DIALOGUE_KIND_LABELS: Record<TGA3DialogueMessage["kind"], string> = {
  assistant_delta: "进度", blackboard_progress: "黑板进度", agent_status: "状态", action_started: "动作开始",
  action_completed: "动作完成", question: "提问", user_message: "用户消息", model_changed: "模型切换",
  paused: "暂停", resumed: "恢复", error: "错误", system: "系统",
};
export const protocolLabel = (value: string) => ({ openai_responses: "OpenAI Responses", openai_chat_completions: "OpenAI Chat Completions", anthropic: "Anthropic Messages" } as Record<string, string>)[value] ?? value;
export const sdkLabel = (value: string) => value === "claude_agent" ? "Claude Agent SDK" : "OpenAI Agents SDK";
export const roleLabel = (agent: Pick<TGA3Agent, "role">) => ROLE_LABELS[agent.role] ?? agent.role;
export const stateLabel = (value: string) => AGENT_STATE_LABELS[value] ?? TASK_STATE_LABELS[value] ?? value;
export function stateTone(value: string): "active" | "waiting" | "success" | "danger" | "muted" {
  if (["running", "starting", "reporting", "finalizing"].includes(value)) return "active";
  if (["waiting_user", "paused", "pause_requested", "idle"].includes(value)) return "waiting";
  if (value === "completed") return "success";
  if (["failed", "cancelled"].includes(value)) return "danger";
  return "muted";
}
export function formatTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
export function bodyText(entry: TGA3BoardEntry): string {
  const body = entry.body;
  return String(body.claim ?? body.conclusion ?? body.advice ?? body.text ?? body.question ?? body.name ?? `${entry.kind}: ${entry.topic}`);
}
export function listStrings(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
export function latestPendingQuestion(dialogue: TGA3DialogueMessage[], taskState: string): TGA3DialogueMessage | null {
  if (taskState !== "waiting_user") return null;
  return [...dialogue].reverse().find((item) => item.kind === "question" && typeof item.payload.question_id === "string") ?? null;
}
