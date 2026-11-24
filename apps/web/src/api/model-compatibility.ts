import type { ProviderProtocol } from "./tga3-config";

export type WorkerRuntime = "codex_cli" | "claude_cli" | "opencode_cli" | "qwen_cli";

/** Check whether a CLI runtime supports the selected provider API protocol. */
export function modelCompatible(runtime: WorkerRuntime, protocol: ProviderProtocol): boolean {
  if (runtime === "claude_cli") return protocol === "anthropic";
  if (runtime === "codex_cli") return protocol === "openai_responses";
  return true;
}
