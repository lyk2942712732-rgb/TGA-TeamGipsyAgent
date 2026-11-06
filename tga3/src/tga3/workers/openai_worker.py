"""Codex CLI entry point shared by Worker and one-shot Reporter."""

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from ..action_logs import read_action_log
from ..action_materials import action_materials
from ..errors import ContractError
from ..experience import check_report_file
from ..scout_intel import read_intels
from .cli_adapter import CodexAdapter
from .prompts import worker_instructions
from .runtime import WorkerSession


async def report() -> None:
    async def emit(method, params):
        print(json.dumps({"method": method, "params": params}, ensure_ascii=False), flush=True)

    adapter = CodexAdapter()
    output = await adapter.run_cycle(
        "Read /workspace/report-input.json and produce the final Markdown report.", emit
    )
    if os.environ.get("TGA3_REPORT_EXPERIENCE") == "1":
        # Correct a missing/malformed file in the existing CLI session, not a new review agent.
        try:
            check_report_file(Path("/workspace"), Path("/evidence"))
        except (OSError, ValueError, TypeError, ContractError) as exc:
            repaired = await adapter.run_cycle(
                f"The required experience-cards.json has a file/citation error: {exc}. "
                "In this SAME session, write the required JSON using the success evidence you already read. "
                "Use {\"cards\":[]} if no transferable method. Do not run new reconnaissance. "
                "Then return the final Markdown report again.", emit)
            if repaired.strip():
                output = repaired
    if not output.strip():
        raise RuntimeError("Reporter returned an empty report")
    Path("/workspace/result/writeup.md").write_text(output, encoding="utf-8")


async def scout() -> None:
    if os.environ.get("TGA3_AOE_PARENT_ID"):
        from .aoe_scout import scout as aoe_scout

        await aoe_scout()
        return

    async def emit(method, params):
        print(json.dumps({"method": method, "params": params}, ensure_ascii=False), flush=True)

    adapter = CodexAdapter()
    intels = Path(os.environ.get("TGA3_INTELS", "/intels"))
    for phase in ("executing", "extracting", "merging"):
        if phase != "executing":
            # The coordinator validates the previous phase before opening the next tool permissions.
            signal = "scout-extract-request" if phase == "extracting" else "scout-merge-request"
            while not (adapter.state_dir / signal).exists():
                await asyncio.sleep(0.1)
        os.environ["TGA3_SCOUT_PHASE"] = phase
        adapter.scout_phase = phase
        adapter.journal.enabled = phase == "executing"
        budget = float(os.environ.get("TGA3_SCOUT_TIMEOUT_SECONDS", "300")) if phase == "executing" else None
        adapter.timeout = budget + 5 if budget is not None else None
        result = {"finish_reason": "completed", "error": None, "budget_seconds": budget}
        try:
            async with asyncio.timeout(budget):
                prompt = ("现在只执行第一轮基础侦察，将生成文件保存到 /workspace。"
                          "系统将正文保存到 /workspace/result，动作汇集到 /logs/scout-actions.json。"
                          "完成后立即结束本轮回复，等待运行时下一条消息切换第二轮，不自行提取 Intel。")
                while True:
                    if phase == "extracting":
                        missing = uncovered_actions(adapter.journal.directory, intels)
                        if not missing:
                            break
                        prompt = (worker_instructions()
                                  + "\n继续当前会话的第二轮。逐条概括以下尚未覆盖的 action，"
                                  "使用材料中的 action_id 引用动作，任务、来源和默认证据由服务端补齐。"
                                  "以下内容为原文片段，不是摘要；遇到截断、缺失或二进制标记时按需读取完整文件。"
                                  "包括失败、超时和未完成记录，准确描述实际结果，不能推断为目标不存在漏洞。"
                                  "使用 scout_write_intel 保存；不要重新侦察。\n"
                                  + json.dumps(action_materials(adapter.journal.directory, adapter.workspace,
                                      UUID(os.environ["TGA3_TASK_ID"]), missing), ensure_ascii=False))
                    elif phase == "merging":
                        missing = unmerged_intels(intels)
                        if not missing:
                            break
                        prompt = (worker_instructions()
                                  + "\n继续原会话的第三轮。以下是尚未合并的原始 Intel，"
                                  "请一次查看整体，将同一对象、同一条件下重复或互补的内容合并。"
                                  "每条原始 ID 必须且只能归入一条最终 Intel；无关内容单独保留。"
                                  "保留不同事实、实际条件、失败边界及先失败后成功的变化，不能新增推论。"
                                  "调用 scout_merge_intel，服务端自动合并来源与证据。"
                                  "仅当需要核实差异时读取 /intels/raw 或相应证据；不要重新侦察。\n"
                                  + json.dumps(missing, ensure_ascii=False))
                    output = await adapter.run_cycle(prompt, emit)
                    if output.strip():
                        await emit("agent.output.delta", {"text": output})
                    if phase == "executing":
                        break
        except TimeoutError:
            result["finish_reason"] = "timeout"
        except Exception as exc:
            result.update(finish_reason="error", error=str(exc).replace(adapter.api_key, "[redacted]"))
        path = adapter.state_dir / f"scout-{phase}-result.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        if phase != "executing" and result["finish_reason"] != "completed":
            return


def phase_intels(intels: Path, *, merged: bool = False) -> list:
    collection = read_intels(intels / ("merged" if merged else "raw"), UUID(os.environ["TGA3_TASK_ID"]),
                             merged=merged)
    return collection.intels if collection else []


def uncovered_actions(logs: Path, intels: Path) -> list[str]:
    covered = {source for item in phase_intels(intels) for source in item.source_action_ids}
    return [action["action_id"] for action in read_action_log(logs)["actions"]
            if action["action_id"] not in covered]


def unmerged_intels(intels: Path) -> list[dict]:
    covered = {source for item in phase_intels(intels, merged=True) for source in item.merged_from_ids}
    return [{"id": item.id, "topic": item.topic, "body": item.body.model_dump()}
            for item in phase_intels(intels) if item.id not in covered]


def main() -> None:
    if os.environ.get("TGA3_AGENT_ROLE") == "reporter":
        asyncio.run(report())
    elif os.environ.get("TGA3_AGENT_ROLE") == "scout":
        asyncio.run(scout())
    else:
        asyncio.run(WorkerSession(CodexAdapter()).run())


if __name__ == "__main__":
    main()
