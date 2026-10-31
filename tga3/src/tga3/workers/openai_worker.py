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
        "开始本题侦察。先阅读钩子注入的黑板与原始输入，实际使用适合本题的现成工具。"
        "每获得一条有用信息，立即调用 scout_collect_intel 保存；结束时系统自动统一写入黑板。"
        "不要调用 blackboard_publish，不要等待最后再收集，不要生成独立简报。", emit
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
