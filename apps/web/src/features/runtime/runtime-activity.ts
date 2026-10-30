import type { TGA3Agent, TGA3BoardEntry, TGA3RuntimeSnapshot, TGA3TaskEvent } from "../../runtime/tga3-runtime";
import { BOARD_KIND_LABELS } from "./tga3-view";

export type RuntimeActivity = { id: string; owner: string; from: string; to?: string; label: string; shortLabel: string; detail: string; at: number };
export type RuntimeActivityEvent = { id: string; at: number; activity?: RuntimeActivity; clearOwner?: string };
export const agentNode = (id: string) => `agent:${id}`;
export const modelNode = (id: string) => `model:${id}`;
const timestamp = (s: string) => Date.parse(s) || 0;
export const compact = (s: string, max = 70) => { const text = s.replace(/\s+/g, " ").trim(); return text.length > max ? `${text.slice(0, max)}…` : text; };

function object(value: unknown): Record<string, unknown> {
  if (typeof value === "string") { try { return object(JSON.parse(value)); } catch { return {}; } }
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
export function actionInput(event: TGA3TaskEvent): string {
  const payload = event.payload;
  const nested = [payload, ...["input", "arguments", "args", "tool_input", "parameters", "request"].map(key => object(payload[key]))];
  const command = nested.flatMap(item => [item.command, item.cmd, item.script]).find(value => typeof value === "string" && value.trim());
  if (typeof command === "string") return command;
  return Object.keys(object(payload.input)).length ? JSON.stringify(object(payload.input), null, 2) : event.text;
}
export function eventAgentId(event: TGA3TaskEvent): string {
  return event.actor.agent_id !== "system" ? event.actor.agent_id : String(event.payload.agent_id ?? event.channel_agent_id);
}
export function toolActivity(event: TGA3TaskEvent): RuntimeActivity | null {
  const id = eventAgentId(event), owner = agentNode(id);
  const base = { id: `event-${event.id}`, owner, at: timestamp(event.created_at), detail: actionInput(event) };
  const name = event.actor.agent_id === "system" ? id : event.actor.display_name;
  if (event.kind === "model_changed") return { ...base, from: owner, to: modelNode(id), shortLabel: "切换模型", label: `${name} 切换模型` };
  if (event.kind === "assistant_delta") return { ...base, from: modelNode(id), to: owner, shortLabel: "模型输出", label: `${name} 收到模型响应`, detail: event.text };
  if (event.kind === "system" && event.payload.attempt) return { ...base, from: owner, shortLabel: "恢复会话", label: `${name} 恢复原 CLI 会话`, detail: event.text };
  if (!["action_started", "action_completed"].includes(event.kind)) return null;
  const tool = String(event.payload.tool ?? event.text), content = `${tool} ${event.text} ${JSON.stringify(event.payload)}`;
  if (/\/skills(?:\/|\b)/i.test(content)) return { ...base, from: "skills", to: owner, shortLabel: "读取 Skill", label: `${name} 读取 Skill` };
  if (/blackboard.*(sync|read)|(?:sync|read).*blackboard/i.test(tool)) return { ...base, from: "blackboard", to: owner, shortLabel: "读取黑板", label: `${name} 读取黑板` };
  if (/blackboard.*publish|publish.*blackboard/i.test(tool)) return { ...base, from: owner, to: "blackboard", shortLabel: "写入黑板", label: `${name} 写入黑板` };
  if (/工作周期|work cycle/i.test(event.text)) return { ...base, from: owner, to: modelNode(id), shortLabel: "请求模型", label: `${name} 请求模型推理` };
  if (/(?:^|[_.:\s-])(bash|shell|shell_exec|execute_command|exec_command|command_execution)(?:$|[_.:\s-])/i.test(tool)) return { ...base, from: owner, shortLabel: event.kind === "action_started" ? "执行命令" : "命令完成", label: `${name} 执行 ${compact(actionInput(event), 58)}` };
  if (/read|write|edit|file/i.test(tool)) return { ...base, from: owner, shortLabel: /write|edit/i.test(tool) ? "写入文件" : "读取文件", label: `${name} ${compact(event.text, 48)}` };
  return { ...base, from: owner, shortLabel: "执行工具", label: `${name} ${compact(event.text, 48)}` };
}

export function runtimeActivityEvents(snapshot: TGA3RuntimeSnapshot): RuntimeActivityEvent[] {
  const events: RuntimeActivityEvent[] = [];
  const started = new Map<string, TGA3TaskEvent>();
  for (const raw of [...snapshot.events].sort((a, b) => a.seq - b.seq)) {
    const call = `${eventAgentId(raw)}:${raw.payload.tool_use_id ?? ""}`;
    if (raw.kind === "action_started") started.set(call, raw);
    const before = raw.kind === "action_completed" ? started.get(call) : undefined;
    const event = before ? { ...raw, payload: { ...before.payload, ...raw.payload } } : raw;
    const owner = agentNode(eventAgentId(event));
    const clear = ["error", "paused"].includes(event.kind) || event.kind === "agent_status" && ["paused", "completed", "stopped", "failed"].includes(String(event.payload.state));
    const activity = toolActivity(event);
    events.push({ id: `event-${event.id}`, at: timestamp(event.created_at), ...(clear ? { clearOwner: owner } : {}), ...(activity ? { activity } : {}) });
  }
  for (const entry of snapshot.blackboard) {
    if (entry.actor.role === "system") continue;
    const owner = entry.actor.role === "user" ? "input" : agentNode(entry.actor.agent_id);
    const shortLabel = `写入 ${BOARD_KIND_LABELS[entry.kind]}`;
    const activity: RuntimeActivity = { id: `board-${entry.id}`, owner, from: owner, to: "blackboard", shortLabel, label: `${entry.actor.display_name} ${shortLabel}`, detail: `#${entry.seq}\n${JSON.stringify(entry.body, null, 2)}`, at: timestamp(entry.created_at) };
    events.push({ id: activity.id, at: activity.at, activity });
  }
  for (const agent of snapshot.agents) {
    const cursor = agent.metadata?.blackboard_cursor, readAt = agent.metadata?.blackboard_read_at;
    if (typeof cursor !== "number" || typeof readAt !== "number") continue;
    const owner = agentNode(agent.agent_id), at = readAt * 1000;
    const activity: RuntimeActivity = { id: `read-${agent.agent_id}-${cursor}-${readAt}`, owner, from: "blackboard", to: owner, shortLabel: "同步黑板", label: `${agent.display_name} 同步黑板至 #${cursor}`, detail: `已确认读取至 #${cursor}；每个 Worker 独立记录已读位置。`, at };
    events.push({ id: activity.id, at, activity });
  }
  return events.sort((a, b) => a.at - b.at || a.id.localeCompare(b.id));
}

export function currentAgentAction(snapshot: TGA3RuntimeSnapshot, agent: TGA3Agent): { text: string; detail: string; pending: boolean } | null {
  if (["paused", "stopped", "completed", "failed", "created"].includes(agent.actual_state)) return null;
  const events = snapshot.events.filter(event => eventAgentId(event) === agent.agent_id);
  const pending = new Map<string, TGA3TaskEvent>();
  const started = new Map<string, TGA3TaskEvent>();
  let latest: TGA3TaskEvent | undefined;
  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    if (event.kind === "agent_status" && ["paused", "stopped", "completed", "failed"].includes(String(event.payload.state))) pending.clear();
    const call = String(event.payload.tool_use_id ?? event.payload.tool ?? "tool");
    if (event.kind === "action_started") { pending.set(call, event); started.set(call, event); }
    if (event.kind === "action_completed") pending.delete(call);
    if (["action_started", "action_completed", "assistant_delta"].includes(event.kind)) latest = event.kind === "action_completed" && started.has(call) ? { ...event, payload: { ...started.get(call)!.payload, ...event.payload } } : event;
  }
  const action = [...pending.values()].pop() ?? latest;
  if (!action) return null;
  const activity = toolActivity(action);
  return { text: activity ? compact(activity.label.replace(`${agent.display_name} `, ""), 56) : compact(action.text), detail: actionInput(action), pending: pending.size > 0 };
}

export function latestAgentEntry(entries: TGA3BoardEntry[], id: string) { return [...entries].reverse().find(entry => entry.actor.agent_id === id); }
