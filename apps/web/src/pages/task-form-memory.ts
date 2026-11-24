import type { AgentRuntime, ProviderProtocol } from "../api/tga3-config";
import type { AgentModelOptions, KnowledgeSource } from "../api/tga3-tasks";
import type { TaskMode } from "../modes";

export type SavedAgentModels = Record<
  string,
  { provider_id: string; model_id: string; protocol: ProviderProtocol; runtime?: AgentRuntime }
>;

export type SavedTaskFormSnapshot = {
  name: string;
  description: string;
  sceneId: TaskMode;
  aoeMinutes: { first: string; second: string; joint: string };
  produceReport: boolean;
  aoeAutoSubmitFlag: boolean;
  knowledgeSource: KnowledgeSource;
  agentModels: SavedAgentModels;
  savedAt: string;
};

export type FieldHistoryEntry = {
  value: string;
  savedAt: string;
};

const LAST_SUBMITTED_KEY = "tga3.newTask.lastSubmitted.v1";
const UNSUBMITTED_DRAFT_KEY = "tga3.newTask.unsubmitted.v1";
const FIELD_HISTORY_KEY = "tga3.newTask.fieldHistory.v1";
const MAX_FIELD_HISTORY = 3;

function safeStorage(): Storage | null {
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    return null;
  }
}

function readJson<T>(key: string): T | null {
  const storage = safeStorage();
  if (!storage) return null;
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown): void {
  const storage = safeStorage();
  if (!storage) return;
  try {
    storage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore storage quota / privacy mode errors.
  }
}

export function loadLastTaskConfig(): SavedTaskFormSnapshot | null {
  const data = readJson<SavedTaskFormSnapshot>(LAST_SUBMITTED_KEY);
  return data && typeof data === "object" && typeof data.sceneId === "string" ? data : null;
}

export function saveLastTaskConfig(snapshot: Omit<SavedTaskFormSnapshot, "savedAt">): void {
  writeJson(LAST_SUBMITTED_KEY, { ...snapshot, savedAt: new Date().toISOString() });
}

export function loadUnsubmittedDraft(): SavedTaskFormSnapshot | null {
  const data = readJson<SavedTaskFormSnapshot>(UNSUBMITTED_DRAFT_KEY);
  if (!data || typeof data !== "object" || typeof data.sceneId !== "string") return null;
  if (!data.name?.trim() && !data.description?.trim()) return null;
  return data;
}

export function saveUnsubmittedDraft(snapshot: Omit<SavedTaskFormSnapshot, "savedAt">): void {
  if (!snapshot.name.trim() && !snapshot.description.trim()) {
    clearUnsubmittedDraft();
    return;
  }
  writeJson(UNSUBMITTED_DRAFT_KEY, { ...snapshot, savedAt: new Date().toISOString() });
}

export function clearUnsubmittedDraft(): void {
  const storage = safeStorage();
  if (!storage) return;
  try {
    storage.removeItem(UNSUBMITTED_DRAFT_KEY);
  } catch {
    // Ignore storage errors.
  }
}

export function loadAllFieldHistory(): Record<string, FieldHistoryEntry[]> {
  const data = readJson<Record<string, FieldHistoryEntry[]>>(FIELD_HISTORY_KEY);
  return data && typeof data === "object" ? data : {};
}

export function recordFieldHistory(fieldKey: string, value: string): Record<string, FieldHistoryEntry[]> {
  const trimmed = value.trim();
  const all = loadAllFieldHistory();
  if (!trimmed) return all;
  const existing = Array.isArray(all[fieldKey]) ? all[fieldKey] : [];
  const deduped = existing.filter((item) => item.value !== trimmed);
  const next = [{ value: trimmed, savedAt: new Date().toISOString() }, ...deduped].slice(0, MAX_FIELD_HISTORY);
  const updated = { ...all, [fieldKey]: next };
  writeJson(FIELD_HISTORY_KEY, updated);
  return updated;
}

export function removeFieldHistory(fieldKey: string, value: string): Record<string, FieldHistoryEntry[]> {
  const all = loadAllFieldHistory();
  const existing = Array.isArray(all[fieldKey]) ? all[fieldKey] : [];
  const next = existing.filter((item) => item.value !== value);
  const updated = { ...all, [fieldKey]: next };
  if (!next.length) delete updated[fieldKey];
  writeJson(FIELD_HISTORY_KEY, updated);
  return updated;
}

export function reconcileAgentModels(
  saved: SavedAgentModels | null | undefined,
  options: AgentModelOptions,
  isCompatible: (
    runtime: "codex_cli" | "claude_cli" | "opencode_cli" | "qwen_cli",
    protocol: ProviderProtocol,
  ) => boolean,
): { models: SavedAgentModels; fallbackAgents: string[] } {
  const models: SavedAgentModels = {};
  const fallbackAgents: string[] = [];
  for (const agent of options.agents) {
    const defaultModel = {
      provider_id: agent.model.provider_id,
      model_id: agent.model.model_id,
      protocol: agent.model.protocol,
    };
    const candidate = saved?.[agent.id];
    if (!candidate) {
      models[agent.id] = defaultModel;
      continue;
    }
    const effectiveRuntime = candidate.runtime ?? agent.runtime;
    const stillValid = options.models.some(
      (choice) =>
        choice.provider_id === candidate.provider_id &&
        choice.model_id === candidate.model_id &&
        choice.protocol === candidate.protocol &&
        choice.ready &&
        isCompatible(effectiveRuntime, choice.protocol),
    );
    if (stillValid) {
      models[agent.id] =
        candidate.runtime && candidate.runtime !== agent.runtime
          ? {
              provider_id: candidate.provider_id,
              model_id: candidate.model_id,
              protocol: candidate.protocol,
              runtime: candidate.runtime,
            }
          : {
              provider_id: candidate.provider_id,
              model_id: candidate.model_id,
              protocol: candidate.protocol,
            };
    } else {
      models[agent.id] = defaultModel;
      fallbackAgents.push(agent.display_name);
    }
  }
  return { models, fallbackAgents };
}

export function formatShortTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}
