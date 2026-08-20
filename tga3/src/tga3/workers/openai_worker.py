"""OpenAI Agents SDK worker running inside its own task container."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .prompts import worker_instructions
from .runtime import AgentAdapter, Emit, WorkerSession


class OpenAIAdapter(AgentAdapter):
    def __init__(self) -> None:
        self.model_name = os.environ["TGA3_MODEL_NAME"]
        self.protocol = os.environ["TGA3_PROVIDER_PROTOCOL"]
        self.api_key = os.environ["TGA3_API_KEY"]
        self.base_url = os.environ.get("TGA3_BASE_URL") or None
        self.max_turns = int(os.environ["TGA3_MAX_TURNS_PER_CYCLE"])
        self.mcp_url = os.environ["TGA3_BLACKBOARD_MCP_URL"]
        self.session: Any = None

    async def set_model(self, params: dict[str, Any]) -> None:
        self.model_name = str(params["model_name"])
        self.protocol = str(params["protocol"])
        self.api_key = str(params["api_key"])
        self.base_url = str(params.get("base_url") or "") or None

    async def run_cycle(self, prompt: str, emit: Emit) -> str:
        from agents import (
            Agent,
            AsyncOpenAI,
            OpenAIChatCompletionsModel,
            OpenAIResponsesModel,
            Runner,
            SQLiteSession,
            function_tool,
            set_tracing_disabled,
        )
        from agents.exceptions import MaxTurnsExceeded
        from agents.mcp import MCPServerStreamableHttp

        set_tracing_disabled(True)
        if self.session is None:
            self.session = SQLiteSession(
                f"{os.environ['TGA3_TASK_ID']}-{os.environ['TGA3_AGENT_ID']}",
                "/workspace/.tga3-openai-session.db",
            )

        @function_tool
        async def shell_exec(command: str, timeout_seconds: int = 120) -> str:
            """Execute a shell command in /workspace and return combined, truncated output."""
            await emit("agent.action.started", {"summary": command[:500], "tool": "shell"})
            process = await asyncio.create_subprocess_shell(
                command,
                cwd="/workspace",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=min(timeout_seconds, 600))
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise RuntimeError(f"command timed out after {timeout_seconds}s") from exc
            text = stdout.decode(errors="replace")
            result = f"exit={process.returncode}\n{text[-100_000:]}"
            await emit(
                "agent.action.completed",
                {"summary": command[:500], "tool": "shell", "exit_code": process.returncode},
            )
            return result

        @function_tool
        async def progress_update(summary: str) -> str:
            """Send the user a concise progress or reasoning summary, never private chain-of-thought."""
            await emit("agent.output.delta", {"text": summary})
            return "Progress update delivered."

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        if self.protocol == "openai_chat_completions":
            model = OpenAIChatCompletionsModel(model=self.model_name, openai_client=client)
        else:
            model = OpenAIResponsesModel(model=self.model_name, openai_client=client)
        async with MCPServerStreamableHttp(
            name="blackboard",
            params={"url": self.mcp_url, "timeout": 30},
            cache_tools_list=True,
            max_retry_attempts=3,
        ) as server:
            agent = Agent(
                name=os.environ["TGA3_AGENT_DISPLAY_NAME"],
                instructions=worker_instructions(),
                model=model,
                tools=[shell_exec, progress_update],
                mcp_servers=[server],
            )
            try:
                result = await Runner.run(
                    agent,
                    prompt,
                    max_turns=self.max_turns,
                    session=self.session,
                )
            except MaxTurnsExceeded:
                return "本轮已达到模型调用上限；工作状态已保留，将在下一周期继续。"
        output = str(result.final_output or "")
        if output.startswith("NEEDS_USER_INPUT:"):
            await emit("agent.needs_user_input", {"question": output.split(":", 1)[1].strip()})
            return ""
        return output


def main() -> None:
    Path("/workspace").mkdir(parents=True, exist_ok=True)
    asyncio.run(WorkerSession(OpenAIAdapter()).run())


if __name__ == "__main__":
    main()
