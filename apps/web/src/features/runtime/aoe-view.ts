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

type Interval = [number, number];

function union(intervals: Interval[]): Interval[] {
  const result: Interval[] = [];
  for (const [start, end] of [...intervals].sort((a, b) => a[0] - b[0])) {
    const last = result[result.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else result.push([start, end]);
  }
  return result;
}

function attemptIntervals(question: AOEQuestion, attempt: AOEAttempt, now: number, stageOnly = false): Interval[] | null {
  const end = attempt.finished_at ?? (attempt.outcome === null ? now : null);
  const start = attempt.started_at;
  if (start == null || end == null || !Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  let intervals: Interval[] = [[start, end]];
  // Swarm refreshes budget timestamps in place, but the work before transition
  // remains part of this Worker's cumulative contribution.
  if (!stageOnly && attempt.previous_started_at != null) {
    const transition = question.swarm?.entered_at ?? start;
    if (!Number.isFinite(attempt.previous_started_at) || transition < attempt.previous_started_at) return null;
    intervals.push([attempt.previous_started_at, transition]);
  }
  for (const pause of attempt.pauses ?? []) {
    if (!Number.isFinite(pause.start) || (pause.end != null && !Number.isFinite(pause.end))) continue;
    const pauseEnd = pause.end ?? end;
    if (pauseEnd < pause.start) continue;
    intervals = intervals.flatMap(([s, e]): Interval[] => {
      if (pause.start >= e || pauseEnd <= s) return [[s, e]];
      return [...(pause.start > s ? [[s, pause.start] as Interval] : []),
        ...(pauseEnd < e ? [[pauseEnd, e] as Interval] : [])];
    });
  }
  return intervals;
}

export function workerElapsed(question: AOEQuestion, agentId: string, now = Date.now() / 1000): number | null {
  const intervals: Interval[] = [];
  for (const attempt of question.attempts.filter(a => a.agent_id === agentId)) {
    const ranges = attemptIntervals(question, attempt, now);
    if (ranges === null) return null; // Incomplete historical records must not lower the mean.
    intervals.push(...ranges);
  }
  return union(intervals).reduce((total, [s, e]) => total + e - s, 0);
}

export function workerRecords(questions: AOEQuestion[], agentId: string, now = Date.now() / 1000) {
  return sortQuestions(questions).flatMap(question => {
    const attempts = question.attempts.filter(a => a.agent_id === agentId);
    if (!attempts.length) return [];
    const success = question.status === "solved" ? [...attempts].reverse().find(a =>
      a.success_credit ?? (a.phase === "reinforcement" ? question.solver_agent_id === a.agent_id : a.outcome === "solved")) : undefined;
    const attempt = success ?? attempts[attempts.length - 1];
    const stage = attemptIntervals(question, attempt, now, true);
    return [{ question, attempt, successful: Boolean(success), seconds: workerElapsed(question, agentId, now),
      stageSeconds: stage === null ? null : union(stage).reduce((total, [s, e]) => total + e - s, 0) }];
  });
}

export function workerWins(questions: AOEQuestion[], agentId: string, now = Date.now() / 1000) {
  return workerRecords(questions, agentId, now).filter(record => record.successful);
}

export function workerDifficultyAverages(questions: AOEQuestion[], agentId: string) {
  const groups = new Map<string, { difficulty: string; count: number; total: number }>();
  for (const record of workerWins(questions, agentId)) {
    if (record.seconds === null) continue;
    const value = record.question.difficulty;
    const key = `${typeof value}:${value}`;
    const group = groups.get(key) ?? { difficulty: value == null ? "未标难度" : String(value), count: 0, total: 0 };
    group.count++; group.total += record.seconds; groups.set(key, group);
  }
  return [...groups.values()].map(group => ({ ...group, averageSeconds: group.total / group.count }))
    .sort((a, b) => a.difficulty.localeCompare(b.difficulty, "zh-CN", { numeric: true }));
}

export function attemptLabel(question: AOEQuestion, attempt: AOEAttempt) {
  if (attempt.phase === "swarm") return "共同攻坚（群殴）";
  if (attempt.phase === "reinforcement" || question.reinforcement?.first_attempt_id === attempt.id
    || question.attempts.some(a => a.phase === "reinforcement" && a.first_attempt_id === attempt.id)) {
    return "首次尝试（增援）";
  }
  return attempt.phase === "joint" ? "共同攻坚" : question.attempts.find(a => a.phase === "solo" || a.previous_phase === "solo")?.id === attempt.id ? "首次尝试" : "第二次尝试";
}

export function attemptGroups(question: AOEQuestion) {
  const groups = new Map<string, { id: string; agents: string[]; label: string; startedAt: number | null;
    finishedAt: number | null; outcomes: Array<AOEAttempt["outcome"] | "promoted"> }>();
  function add(attempt: AOEAttempt, promoted = false) {
    const key = attempt.phase === "joint" || attempt.phase === "swarm" ? "joint"
      : attempt.phase === "reinforcement" ? `solo:${attempt.first_attempt_id ?? question.reinforcement?.first_attempt_id
        ?? question.attempts.find(a => a.phase === "solo" || a.previous_phase === "solo")?.id ?? "first"}`
      : `solo:${attempt.id}`;
    const group = groups.get(key) ?? { id: key, agents: [], label: attemptLabel(question, attempt),
      startedAt: null, finishedAt: null, outcomes: [] };
    if (!group.agents.includes(attempt.agent_id)) group.agents.push(attempt.agent_id);
    if (attempt.phase === "swarm" || attempt.phase === "reinforcement") group.label = attemptLabel(question, attempt);
    if (attempt.started_at != null) group.startedAt = Math.min(group.startedAt ?? Infinity, attempt.started_at);
    if (attempt.finished_at != null) group.finishedAt = Math.max(group.finishedAt ?? -Infinity, attempt.finished_at);
    group.outcomes.push(promoted ? "promoted" : attempt.outcome);
    groups.set(key, group);
  }
  for (const attempt of question.attempts) {
    if (attempt.previous_phase) add({ ...attempt, phase: attempt.previous_phase as AOEAttempt["phase"],
      started_at: attempt.previous_started_at ?? null,
      finished_at: question.swarm?.entered_at ?? attempt.started_at ?? undefined }, true);
    add(attempt);
  }
  return [...groups.values()].map(group => ({ ...group,
    outcome: group.outcomes.includes("solved") ? "已解出" : group.outcomes.includes(null) ? "进行中"
      : group.outcomes.includes("promoted") ? "转入共同攻坚（群殴）"
      : group.outcomes.includes("timeout") ? "预算已到" : group.outcomes.includes("error") ? "执行失败" : "已中断",
    finishedAt: group.outcomes.includes(null) ? null : group.finishedAt,
  }));
}

export function formatElapsed(seconds: number | null) {
  if (seconds === null) return "未记录";
  const total = Math.round(seconds), minutes = Math.floor(total / 60), rest = total % 60;
  return minutes ? `${minutes}分${String(rest).padStart(2, "0")}秒` : `${rest}秒`;
}
