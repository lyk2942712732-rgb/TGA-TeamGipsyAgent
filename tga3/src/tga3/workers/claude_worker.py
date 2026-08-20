"""Claude Agent SDK worker running inside its own task container."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .prompts import worker_instructions
from .runtime import AgentAdapter, Emit, WorkerSession


class ClaudeAdapter(AgentAdapter):
    def __init__(self) -> None:
        self.model_name = os.environ["TGA3_MODEL_NAME"]
        self.api_key = os.environ["TGA3_API_KEY"]
        self.base_url = os.environ.get("TGA3_BASE_URL") or None
        self.max_turns = int(os.environ.get("TGA3_MAX_TURNS_PER_CYCLE", "3"))
        self.mcp_url = os.environ["TGA3_BLACKBOARD_MCP_URL"]
        self.session_id: str | None = None

    async def set_model(self, params: dict[str, Any]) -> None:
        if str(params["protocol"]) != "anthropic":
            raise ValueError("Claude Agent SDK requires an anthropic provider")
        self.model_name = str(params["model_name"])
        self.api_key = str(params["api_key"])
        self.base_url = str(params.get("base_url") or "") or None

    async def run_cycle(self, prompt: str, emit: Emit) -> str:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
            query,
        )

        os.environ["ANTHROPIC_API_KEY"] = self.api_key
        if self.base_url:
            os.environ["ANTHROPIC_BASE_URL"] = self.base_url
        options = ClaudeAgentOptions(
            cwd="/workspace",
            model=self.model_name,
            system_prompt=worker_instructions(),
            tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
            mcp_servers={"blackboard": {"type": "http", "url": self.mcp_url}},
            strict_mcp_config=True,
            allowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep", "mcp__blackboard__*"],
            disallowed_tools=["AskUserQuestion"],
            permission_mode="bypassPermissions",
            max_turns=self.max_turns,
            resume=self.session_id,
        )
        text_parts: list[str] = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        await emit(
                            "agent.action.started",
                            {"summary": block.name, "tool": block.name, "input": block.input},
                        )
                    elif isinstance(block, ToolResultBlock):
                        await emit(
                            "agent.action.completed",
                            {"summary": "工具执行完成", "tool_use_id": block.tool_use_id, "error": block.is_error},
                        )
                    elif isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(message, ResultMessage):
                self.session_id = message.session_id
                if getattr(message, "result", None):
                    text_parts.append(str(message.result))
        output = "\n".join(text_parts).strip()
        marker = "NEEDS_USER_INPUT:"
        if marker in output:
            question = output.rsplit(marker, 1)[1].strip().splitlines()[0]
            await emit("agent.needs_user_input", {"question": question})
            return output.split(marker, 1)[0].strip()
        return output


def main() -> None:
    Path("/workspace").mkdir(parents=True, exist_ok=True)
    asyncio.run(WorkerSession(ClaudeAdapter()).run())


if __name__ == "__main__":
    main()
