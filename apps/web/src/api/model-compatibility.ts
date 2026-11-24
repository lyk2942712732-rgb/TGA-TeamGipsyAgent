import type { AgentRuntime, ProviderProtocol } from "./tga3-config";

export type WorkerRuntime = AgentRuntime;

export const ALL_RUNTIMES: readonly WorkerRuntime[] = [
  "codex_cli",
  "claude_cli",
  "opencode_cli",
  "qwen_cli",
] as const;

export const RUNTIME_LABELS: Record<WorkerRuntime, string> = {
  codex_cli: "Codex CLI",
  claude_cli: "Claude Code CLI",
  opencode_cli: "OpenCode CLI",
  qwen_cli: "Qwen Code CLI",
};

export const RUNTIME_SHORT_LABELS: Record<WorkerRuntime, string> = {
  codex_cli: "Codex",
  claude_cli: "Claude",
  opencode_cli: "OpenCode",
  qwen_cli: "Qwen",
};

export const RUNTIME_PROTOCOL_SUMMARY: Record<WorkerRuntime, string> = {
  codex_cli: "仅 Responses",
  claude_cli: "仅 Anthropic",
  opencode_cli: "全协议支持",
  qwen_cli: "全协议支持",
};

export const RUNTIME_SUPPORTED_PROTOCOLS: Record<WorkerRuntime, ProviderProtocol[]> = {
  codex_cli: ["openai_responses"],
  claude_cli: ["anthropic"],
  opencode_cli: ["openai_chat_completions", "openai_responses", "anthropic"],
  qwen_cli: ["openai_chat_completions", "openai_responses", "anthropic"],
};

/** Check whether a CLI runtime supports the selected provider API protocol. */
export function modelCompatible(runtime: WorkerRuntime, protocol: ProviderProtocol): boolean {
  if (runtime === "claude_cli") return protocol === "anthropic";
  if (runtime === "codex_cli") return protocol === "openai_responses";
  return true;
}

/** Pick the best protocol when switching a slot onto a new CLI runtime. */
export function preferredProtocolForRuntime(
  runtime: WorkerRuntime,
  currentProtocol: ProviderProtocol,
  availableProtocols: ProviderProtocol[],
): ProviderProtocol {
  const supported = RUNTIME_SUPPORTED_PROTOCOLS[runtime];
  if (supported.includes(currentProtocol)) return currentProtocol;
  return supported.find((proto) => availableProtocols.includes(proto)) ?? supported[0];
}
