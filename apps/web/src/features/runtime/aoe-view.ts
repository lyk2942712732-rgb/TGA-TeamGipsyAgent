import type { AOEAttempt, AOEOverview, AOEQuestion } from "../../runtime/tga3-runtime";

export type QuestionFilter = "all" | "solved" | "attempted" | "unattempted";
export const QUESTION_FILTERS: Array<[QuestionFilter, string]> = [
  ["all", "全部状态"], ["solved", "已解出"], ["attempted", "已尝试未解出"], ["unattempted", "未尝试"],
];

export function matchesQuestionFilter(question: AOEQuestion, filter: QuestionFilter) {
  const tried = question.attempts.length > 0 || question.attempted_by.length > 0 || question.status === "running";
  return filter === "all" || (filter === "solved" ? question.status === "solved"
    : question.status !== "solved" && (filter === "attempted" ? tried : !tried));
}

export function solvedAt(question: AOEQuestion) {
  return question.solved_at ?? Math.max(0, ...question.attempts.filter(a => a.outcome === "solved").map(a => a.finished_at ?? 0));
}

export function sortQuestions(questions: AOEQuestion[]) {
  const priority = (q: AOEQuestion) => q.status === "running" ? 0 : q.status === "solved" ? 1 : 2;
  return [...questions].sort((a, b) => priority(a) - priority(b)
    || (b.status === "solved" ? solvedAt(b) : b.registered_at ?? 0) - (a.status === "solved" ? solvedAt(a) : a.registered_at ?? 0));
}

export function aoeBudgets(overview: AOEOverview) {
  return overview.attempt_budgets ?? { first: overview.attempt_seconds, second: overview.attempt_seconds, joint: overview.attempt_seconds };
}

export function workerWins(questions: AOEQuestion[], agentId: string) {
  return sortQuestions(questions.filter(q => q.status === "solved")).flatMap(question => {
    const attempt = [...question.attempts].reverse().find(a => a.agent_id === agentId && a.outcome === "solved");
    if (!attempt) return [];
    const seconds = attempt.started_at != null && attempt.finished_at != null
      ? Math.max(0, attempt.finished_at - attempt.started_at) : null;
    return [{ question, attempt, seconds }];
  });
}

export function attemptLabel(question: AOEQuestion, attempt: AOEAttempt) {
  return attempt.phase === "joint" ? "共同攻坚" : question.attempts.find(a => a.phase === "solo")?.id === attempt.id ? "首次尝试" : "第二次尝试";
}

export function formatElapsed(seconds: number | null) {
  if (seconds === null) return "未记录";
  const total = Math.round(seconds), minutes = Math.floor(total / 60), rest = total % 60;
  return minutes ? `${minutes}分${String(rest).padStart(2, "0")}秒` : `${rest}秒`;
}
