"""Codex CLI entry point shared by Worker and one-shot Reporter."""

import asyncio
import json
import os
from pathlib import Path

from .cli_adapter import CodexAdapter
from .runtime import WorkerSession


async def report() -> None:
    async def emit(method, params):
        print(json.dumps({"method": method, "params": params}, ensure_ascii=False), flush=True)

    output = await CodexAdapter().run_cycle(
        "Read /workspace/report-input.json and produce the final Markdown report.", emit
    )
    if not output.strip():
        raise RuntimeError("Reporter returned an empty report")
    Path("/workspace/writeup.md").write_text(output, encoding="utf-8")


async def scout() -> None:
    async def emit(method, params):
        print(json.dumps({"method": method, "params": params}, ensure_ascii=False), flush=True)

    output = await CodexAdapter().run_cycle(
        "开始本题侦察。先读取黑板与原始输入，实际使用适合本题的现成工具，及时发布 Intel。"
        "在总预算内完成交接；不要生成独立简报，也不要进入持续工作循环。", emit
    )
    if output.strip():
        await emit("agent.output.delta", {"text": output})


def main() -> None:
    if os.environ.get("TGA3_AGENT_ROLE") == "reporter":
        asyncio.run(report())
    elif os.environ.get("TGA3_AGENT_ROLE") == "scout":
        asyncio.run(scout())
    else:
        asyncio.run(WorkerSession(CodexAdapter()).run())


if __name__ == "__main__":
    main()
