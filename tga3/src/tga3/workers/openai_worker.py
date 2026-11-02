"""Codex CLI entry point shared by Worker and one-shot Reporter."""

import asyncio
import json
import os
from pathlib import Path

from .cli_adapter import CodexAdapter
from .prompts import worker_instructions
from .runtime import WorkerSession


async def report() -> None:
    async def emit(method, params):
        print(json.dumps({"method": method, "params": params}, ensure_ascii=False), flush=True)

    output = await CodexAdapter().run_cycle(
        "Read /workspace/report-input.json and produce the final Markdown report.", emit
    )
    if not output.strip():
        raise RuntimeError("Reporter returned an empty report")
    Path("/workspace/art/writeup.md").write_text(output, encoding="utf-8")


async def scout() -> None:
    async def emit(method, params):
        print(json.dumps({"method": method, "params": params}, ensure_ascii=False), flush=True)

    adapter = CodexAdapter()
    intels = Path(os.environ.get("TGA3_INTELS", "/intels"))
    for phase, budget_key in (("executing", "TGA3_SCOUT_TIMEOUT_SECONDS"),
                              ("extracting", "TGA3_SCOUT_EXTRACT_TIMEOUT_SECONDS")):
        if phase == "extracting":
            # Wait until the coordinator has recorded the first result and opened the Intel tool.
            while not (adapter.state_dir / "scout-extract-request").exists():
                await asyncio.sleep(0.1)
        os.environ["TGA3_SCOUT_PHASE"] = phase
        adapter.scout_phase = phase
        adapter.journal.enabled = phase == "executing"
        budget = float(os.environ.get(budget_key, "300" if phase == "executing" else "180"))
        adapter.timeout = budget + 5
        result = {"finish_reason": "completed", "error": None, "budget_seconds": budget}
        try:
            async with asyncio.timeout(budget):
                prompt = ("执行第一轮基础侦察，将生成文件保存到 /workspace/art。执行记录由系统保存到 /artifacts。"
                          "达到基础侦察目标后结束本轮；随后会在当前会话继续整理 Intel。")
                while True:
                    if phase == "extracting":
                        missing = uncovered_artifacts(adapter.journal.directory, intels)
                        prompt = (worker_instructions()
                                  + "\n继续当前会话的第二轮。逐条概括以下尚未覆盖的 artifact JSON，"
                                  "每条至少对应一份 Intel，evidence_paths 必须引用该 JSON 文件。"
                                  "包括失败、超时和未完成记录，准确描述实际结果，不能推断为目标不存在漏洞。"
                                  "使用 scout_write_intel 保存；不要重新侦察。\n"
                                  + json.dumps(missing, ensure_ascii=False))
                    output = await adapter.run_cycle(prompt, emit)
                    if output.strip():
                        await emit("agent.output.delta", {"text": output})
                    if phase == "executing" or not uncovered_artifacts(adapter.journal.directory, intels):
                        break
        except TimeoutError:
            result["finish_reason"] = "timeout"
        except Exception as exc:
            result.update(finish_reason="error", error=str(exc).replace(adapter.api_key, "[redacted]"))
        path = adapter.state_dir / f"scout-{phase}-result.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)


def uncovered_artifacts(artifacts: Path, intels: Path) -> list[str]:
    covered = set()
    for path in intels.glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        covered.update(Path(relative).as_posix().removeprefix("./")
                       for relative in document.get("input", {}).get("evidence_paths", []))
    return [path.relative_to(artifacts).as_posix() for path in sorted(artifacts.rglob("*.json"))
            if path.relative_to(artifacts).as_posix() not in covered]


def main() -> None:
    if os.environ.get("TGA3_AGENT_ROLE") == "reporter":
        asyncio.run(report())
    elif os.environ.get("TGA3_AGENT_ROLE") == "scout":
        asyncio.run(scout())
    else:
        asyncio.run(WorkerSession(CodexAdapter()).run())


if __name__ == "__main__":
    main()
