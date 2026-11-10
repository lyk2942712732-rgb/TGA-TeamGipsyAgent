import { describe, expect, it } from "vitest";
import type { AOEAttempt, AOEQuestion } from "../../runtime/tga3-runtime";
import { attemptGroups, attemptLabel, workerDifficultyAverages, workerElapsed, workerRecords, workerWins } from "./aoe-view";

function attempt(start: number | null, end: number, extra: Partial<AOEAttempt> = {}): AOEAttempt {
  return { id: `${start}-${end}`, agent_id: "b", phase: "solo", started_at: start,
    finished_at: end, deadline: end + 1, outcome: "solved", ...extra };
}
function question(id: string, attempts: AOEAttempt[], extra: Partial<AOEQuestion> = {}): AOEQuestion {
  return { question_id: id, parent_task_id: "batch", title: id, source_key: id, description: "q",
    status: "solved", difficulty: "easy", connection_info: "", submission_instructions: "", attempts,
    attempted_by: [], current_agents: [], attachments: [], flag: null, blackboard_seq: 0,
    report_state: "completed", ...extra };
}

describe("Worker cumulative contribution", () => {
  it("groups a joint pair into one numbered stage after two solo attempts", () => {
    const q = question("joint", [attempt(0, 100, { id: "solo1", agent_id: "a", outcome: "timeout" }),
      attempt(200, 300, { id: "solo2", agent_id: "b", outcome: "timeout" }),
      attempt(400, 500, { id: "joint1", agent_id: "c", phase: "joint" }),
      attempt(400, 500, { id: "joint2", agent_id: "d", phase: "joint" })]);
    const groups = attemptGroups(q);
    expect(groups).toHaveLength(3);
    expect(groups[2]).toMatchObject({ agents: ["c", "d"], label: "共同攻坚", outcome: "已解出" });
  });
  it("merges owner/helper and all swarm participants while retaining genuine earlier stages", () => {
    const first = attempt(0, 100, { id: "first", agent_id: "a" });
    const helper = attempt(60, 100, { id: "help", phase: "reinforcement", first_attempt_id: "first" });
    const group = attemptGroups(question("helped", [first, helper]));
    expect(group).toHaveLength(1);
    expect(group[0]).toMatchObject({ agents: ["a", "b"], label: "首次尝试（增援）", outcome: "已解出" });
    const q = question("swarm", [attempt(300, 600, { phase: "swarm", agent_id: "a", id: "a",
      previous_phase: "joint", previous_started_at: 200 }),
      attempt(300, 600, { phase: "swarm", agent_id: "b", id: "b" }),
      attempt(300, 600, { phase: "swarm", agent_id: "c", id: "c" }),
      attempt(300, 600, { phase: "swarm", agent_id: "d", id: "d" })],
    { swarm: { entered_at: 300, started_at: 300, deadline: 1000 } });
    expect(attemptGroups(q)).toHaveLength(1);
    expect(attemptGroups(q)[0]).toMatchObject({ agents: ["a", "b", "c", "d"], label: "共同攻坚（群殴）" });
  });
  it("credits the owner for either solver, but excludes helper time and averages when owner solved", () => {
    const owner = attempt(0, 900, { id: "owner", agent_id: "a", success_credit: true });
    const helper = attempt(660, 900, { id: "helper", phase: "reinforcement", first_attempt_id: "owner", success_credit: false });
    const q = question("owner solved", [owner, helper], { solver_agent_id: "a" });
    expect(workerWins([q], "a")[0].seconds).toBe(900);
    expect(workerWins([q], "b")).toEqual([]);
    expect(workerDifficultyAverages([q], "b")).toEqual([]);
    expect(workerRecords([q], "b")[0].successful).toBe(false);
    const helperSolved = question("helper solved", [owner, { ...helper, success_credit: true }], { solver_agent_id: "b" });
    expect(workerWins([helperSolved], "a")[0].seconds).toBe(900);
    expect(workerWins([helperSolved], "b")[0].seconds).toBe(240);
  });
  it("classifies reinforcement as a first attempt for both owner and helper, and swarm as joint", () => {
    const original = attempt(0, 900, { id: "original" });
    const helper = attempt(660, 900, { id: "helper", phase: "reinforcement", first_attempt_id: "original" });
    const q = question("first with help", [original, helper]);
    expect(attemptLabel(q, original)).toBe("首次尝试（增援）");
    expect(attemptLabel(q, helper)).toBe("首次尝试（增援）");
    expect(attemptLabel(q, attempt(1000, 1200, { phase: "swarm" }))).toBe("共同攻坚（群殴）");
  });
  it("adds reinforcement and later joint work, excluding the time spent waiting for another Worker", () => {
    const q = question("resumed", [attempt(660, 900, { phase: "reinforcement", outcome: "timeout" }),
      attempt(1500, 2100, { phase: "joint" })]);
    const [win] = workerWins([q], "b");
    expect(win.seconds).toBe(840); // 4 minutes + 10 minutes, never the 10-minute gap.
    expect(win.stageSeconds).toBe(600);
  });
  it("retains pre-swarm work while subtracting acknowledged pauses across both phases", () => {
    const q = question("swarm", [attempt(300, 600, { phase: "swarm", previous_started_at: 100,
      previous_phase: "solo", pauses: [{ start: 150, end: 200 }, { start: 350, end: 400 }] })],
    { swarm: { entered_at: 300, started_at: 300, deadline: 1000 } });
    const [win] = workerWins([q], "b");
    expect(win.seconds).toBe(400);
    expect(win.stageSeconds).toBe(250);
    const { swarm: _, ...legacy } = q;
    expect(workerElapsed(legacy, "b")).toBe(400);
  });
  it("merges overlapping records and does not count an open pause as work", () => {
    expect(workerElapsed(question("overlap", [attempt(0, 100), attempt(50, 150)]), "b")).toBe(150);
    const q = question("active", [attempt(100, 200, { outcome: null, finished_at: undefined,
      pauses: [{ start: 300, end: null }] })], { status: "running" });
    expect(workerElapsed(q, "b", 1000)).toBe(200);
    expect(workerElapsed(q, "b", 2000)).toBe(200);
  });
  it("averages full cumulative contributions of this Worker's wins only, excluding missing data", () => {
    const resumed = question("resumed", [attempt(660, 900, { phase: "reinforcement", outcome: "timeout" }),
      attempt(1500, 2100, { phase: "joint" })]);
    const questions = [resumed, question("solo", [attempt(0, 360)]),
      question("failed", [attempt(0, 720, { outcome: "timeout" })], { status: "abandoned" }),
      question("peer", [attempt(0, 20, { agent_id: "a" })]),
      question("missing", [attempt(null, 60)], { difficulty: "hard" })];
    expect(workerDifficultyAverages(questions, "b")).toEqual([
      { difficulty: "easy", count: 2, total: 1200, averageSeconds: 600 },
    ]);
    expect(workerElapsed(question("partial", [attempt(null, 100, { outcome: "timeout" }), attempt(200, 300)]), "b"))
      .toBeNull();
  });
  it("keeps failed participation when a peer later solves it, and lists each question once", () => {
    const peer = question("peer", [attempt(0, 100, { outcome: "timeout" }),
      attempt(200, 250, { outcome: "error" }), attempt(300, 500, { agent_id: "a" })]);
    const [record] = workerRecords([peer], "b");
    expect(record.successful).toBe(false);
    expect(record.seconds).toBe(150);
    expect(workerRecords([peer], "never-tried")).toEqual([]);
  });
});
