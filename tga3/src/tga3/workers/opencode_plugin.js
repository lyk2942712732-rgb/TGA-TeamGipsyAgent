// OpenCode's native plugin hooks share the same resident HookService as other CLIs.
export const TGA = async () => {
  const started = new Map();
  let failedContext = "";
  async function checkpoint(event) {
    const response = await fetch(`http://127.0.0.1:${process.env.TGA3_HOOK_PORT}/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cwd: process.env.TGA3_WORKSPACE, _tga_generation: process.env.TGA3_CLI_GENERATION, ...event }),
      signal: AbortSignal.timeout(14000),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(`TGA checkpoint failed: ${data.error}; retry after recovery`);
    return data.hookSpecificOutput ?? {};
  }
  const eventFor = (name, input, args, response) => ({ hook_event_name: name, session_id: input.sessionID,
    tool_use_id: input.callID, tool_name: input.tool, tool_input: args, tool_response: response,
    _tga_defer_context: true });
  return {
    "experimental.chat.system.transform": async (input, output) => {
      const result = await checkpoint({ hook_event_name: "UserPromptSubmit", session_id: input.sessionID });
      if (result.additionalContext) output.system.push(result.additionalContext);
    },
    "tool.execute.before": async (input, output) => {
      if (failedContext) { const text = failedContext; failedContext = ""; throw new Error(text); }
      const result = await checkpoint(eventFor("PreToolUse", input, output.args));
      if (result.permissionDecision === "deny") throw new Error([result.permissionDecisionReason, result.additionalContext].filter(Boolean).join("\n"));
      started.set(input.callID, { input, args: output.args });
    },
    "tool.execute.after": async (input, output) => {
      const result = await checkpoint(eventFor("PostToolUse", input, input.args,
        { stdout: output.output, exit_code: output.metadata?.exit ?? 0 }));
      started.delete(input.callID);
      if (result.additionalContext) output.output += `\n\n${result.additionalContext}`;
    },
    event: async ({ event }) => {
      const part = event.type === "message.part.updated" ? event.properties.part : null;
      if (part?.type !== "tool" || part.state.status !== "error") return;
      const pending = started.get(part.callID);
      if (!pending) return;
      started.delete(part.callID);
      const result = await checkpoint(eventFor("PostToolUseFailure", pending.input, pending.args,
        { isError: true, content: part.state.error }));
      failedContext += result.additionalContext ?? "";
    },
  };
};
