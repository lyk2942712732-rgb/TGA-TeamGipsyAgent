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

    extracting = os.environ.get("TGA3_SCOUT_PHASE") == "extracting"
    prompt = (
        "现在执行 Scout 第二轮：只读 /artifacts 中的执行日志和留存文件，提取可复用的 Intel。"
        "使用 scout_write_intel 参数化工具保存到 /intels。只需填写 task_id、key、claim、detail、topic、evidence_paths。"
        "所有有依据的 Intel 保存后结束，系统会导入黑板；不要重新侦察或连接目标。"
        if extracting else
        "现在执行 Scout 第一轮：根据题目与附件实际执行基础侦察，把工具输出和可复用文件保存到 /artifacts。"
        "只执行和留存材料，不提取或整理 Intel，不调用任何 Intel/黑板写入工具；达到基础侦察目标后结束。"
    )
    output = await CodexAdapter().run_cycle(prompt, emit)
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
