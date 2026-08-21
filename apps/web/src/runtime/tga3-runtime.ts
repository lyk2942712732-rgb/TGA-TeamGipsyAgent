import { requestJson } from "../api/client";
import type { RuntimeEvent, RuntimeFinding, RuntimeSolver, RuntimeStore } from "../features/runtime/models/types";

type Task = { id: string; title: string; scene_id: string; state: string; blackboard_seq: number; dialogue_seq: number; final_snapshot_seq?: number | null; created_at: string; updated_at: string };
type Agent = { agent_id: string; sdk: string; desired_state: string; actual_state: string; provider_id: string; model_id: string; protocol: string; display_name: string; role: string; runtime_location: string; updated_at: string; last_error?: string | null };
type Detail = { task: Task; agents: Agent[] };
type Actor = { agent_id: string; display_name: string; role: string; sdk?: string | null; model?: string | null };
type BoardEntry = { id: string; seq: number; actor: Actor; kind: string; topic: string; body: Record<string, unknown>; created_at: string };
type Dialogue = { id: string; seq: number; channel_agent_id: string; actor: Actor; kind: string; text: string; payload: Record<string, unknown>; created_at: string };

export async function loadTGA3Runtime(taskId: string): Promise<RuntimeStore> {
  const [detail, board, dialogue] = await Promise.all([
    requestJson<Detail>(`/api/v3/tasks/${encodeURIComponent(taskId)}`),
    requestJson<{ entries: BoardEntry[] }>(`/api/v3/tasks/${encodeURIComponent(taskId)}/blackboard`),
    requestJson<Dialogue[]>(`/api/v3/tasks/${encodeURIComponent(taskId)}/dialogue`),
  ]);
  const { task, agents } = detail;
  const solversById = Object.fromEntries(agents.map((agent): [string, RuntimeSolver] => [agent.agent_id, {
    taskId: task.id, solverId: agent.agent_id, definitionId: agent.display_name, orchestrationRole: agent.role,
    specialties: agent.role === "worker" ? [agent.sdk, "container"] : [agent.sdk, "host"], parentSolverId: agent.role === "worker" ? "supervisor" : null,
    assignedIntentId: null, status: agent.actual_state, currentSummary: agent.last_error ?? statusSummary(agent),
    modelSnapshot: { provider_id: agent.provider_id, model_id: agent.model_id, protocol: agent.protocol, sdk: agent.sdk, runtime_location: agent.runtime_location },
    capabilityBinding: { host_capability_ids: agent.role === "worker" ? [] : ["blackboard", "skills"], kali: agent.role === "worker" ? { capabilities: ["shell", "files", "network"], profile_id: "tga3-worker" } : {} },
    budgetUsage: {}, timestamps: { updated_at: agent.updated_at },
  }]));
  const findings = board.entries.filter((entry) => entry.kind === "finding");
  const findingsById = Object.fromEntries(findings.map((entry): [string, RuntimeFinding] => [entry.id, { findingId: entry.id, title: String(entry.body.claim ?? entry.topic), descriptionPreview: String(entry.body.detail ?? ""), target: entry.topic, severity: "info", status: "verified", evidenceClaimIds: [], createdBySolverId: entry.actor.agent_id, createdAt: entry.created_at, reviewedAt: entry.created_at }]));
  const eventRows = [
    ...dialogue.map((item) => ({ id: item.id, createdAt: item.created_at, solverId: item.channel_agent_id || item.actor.agent_id, type: dialogueType(item.kind), payload: { ...item.payload, summary: item.text, text: item.text, actor: item.actor } })),
    ...board.entries.map((item) => ({ id: item.id, createdAt: item.created_at, solverId: item.actor.agent_id, type: boardType(item.kind), payload: { ...item.body, summary: boardSummary(item), topic: item.topic, actor: item.actor } })),
  ].sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  const eventsBySeq = Object.fromEntries(eventRows.map((row, index): [number, RuntimeEvent] => [index + 1, { schemaVersion: 6, id: row.id, taskId: task.id, seq: index + 1, type: row.type, solverId: row.solverId, intentId: null, payload: row.payload, createdAt: row.createdAt }]));
  const solverIds = Object.keys(solversById);
  return {
    schemaVersion: 6, task: { id: task.id, name: task.title, mode: task.scene_id, goal: board.entries.find((entry) => entry.kind === "user_prompt")?.body.text as string ?? task.title, prompt: "", schemaVersion: 3, raw: task },
    session: { status: task.state, supervisorSolverId: "supervisor", activeSolverCount: agents.filter((agent) => ["starting", "running"].includes(agent.actual_state)).length, maxActiveWorkers: 2, taskBudgetUsage: {}, stopReason: task.state === "failed" ? "任务运行失败" : null, timestamps: { created_at: task.created_at, updated_at: task.updated_at }, turnCount: 0, maxTurns: 0 },
    team: { taskId: task.id, status: task.state, supervisorSolverId: "supervisor", maxActiveWorkers: 2, maxTotalSolvers: agents.length, activeSolverCount: agents.filter((agent) => ["starting", "running"].includes(agent.actual_state)).length, solverIds, version: task.blackboard_seq + task.dialogue_seq, timestamps: { updated_at: task.updated_at } },
    solversById, intentsById: {}, workerResultsById: {}, knowledgeById: {}, artifactsById: {}, evidenceById: {}, findingsById, actionsById: {}, approvalsById: {}, retrievalById: {}, eventsBySeq,
    globalPlan: { blackboard_seq: task.blackboard_seq, dialogue_seq: task.dialogue_seq }, modeProjection: { challenge: {}, flags: board.entries.filter((entry) => entry.kind === "final_candidate").map((entry) => entry.body), artifactIndexes: [] },
    latestSeq: eventRows.length, eventHistoryHasMore: false, entitySequence: { solversById: {}, intentsById: {}, workerResultsById: {}, knowledgeById: {}, evidenceById: {}, findingsById: {}, approvalsById: {}, retrievalById: {} },
  };
}

const statusSummary = (agent: Agent) => `${agent.display_name} 当前状态：${agent.actual_state}`;
const dialogueType = (kind: string) => ({ user_message: "USER_SOLVER_MESSAGE", question: "USER_INPUT_REQUIRED", agent_status: "SOLVER_STATUS_CHANGED", model_changed: "SOLVER_MODEL_CHANGED", paused: "SOLVER_CONTROL_CHANGED", resumed: "SOLVER_CONTROL_CHANGED", action_started: "TOOL_ACTION_REQUESTED", action_completed: "TOOL_COMPLETED", blackboard_progress: "SUPERVISOR_DECIDED", assistant_delta: "SUPERVISOR_DECIDED" } as Record<string, string>)[kind] ?? "DIALOGUE_MESSAGE";
const boardType = (kind: string) => ({ user_prompt: "USER_SOLVER_MESSAGE", user_file: "INPUT_FILE_ADDED", supervisor_advice: "SUPERVISOR_DECIDED", finding: "FINDING_CONFIRMED", qa: "USER_INPUT_ANSWERED", final_candidate: "WORKER_ATTEMPT_COMPLETED" } as Record<string, string>)[kind] ?? "BLACKBOARD_UPDATED";
const boardSummary = (entry: BoardEntry) => String(entry.body.claim ?? entry.body.conclusion ?? entry.body.text ?? entry.body.question ?? entry.body.name ?? `${entry.kind}: ${entry.topic}`);
