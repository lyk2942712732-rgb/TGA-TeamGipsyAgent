"""Four AOE work phases, one Codex adapter, native session and container."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from ..action_logs import read_action_log
from ..action_materials import action_materials
from ..scout_intel import read_intels
from .action_journal import ActionJournal
from .cli_adapter import CodexAdapter


def instructions() -> str:
    phase = os.environ.get("TGA3_SCOUT_PHASE", "collecting")
    workspace = os.environ.get("TGA3_WORKSPACE", "/workspace")
    inputs = os.environ.get("TGA3_INPUTS", "/inputs")
    logs = os.environ.get("TGA3_LOGS", "/logs")
    intels = os.environ.get("TGA3_INTELS", "/intels")
    common = (
        os.environ["TGA3_SYSTEM_PROMPT"] + "\nAOE Scout: the pre-phase and three rounds are work divisions "
        "inside ONE native CLI session/container. Never launch a separate agent/session for a phase or question. "
        f"Current phase: {phase}. Task binding: {os.environ['TGA3_TASK_ID']}. "
        f"Working directory: {workspace}. Inputs: {inputs}. Logs: {logs}. Intel: {intels}. "
        "Recorded artifact paths are relative to this question's working directory. Use these exact paths; "
        "the old /workspace,/inputs,/logs mounts refer to batch collection when working on a question. "
        "Native Skills are under /skills; read only useful SKILL.md and referenced documents. "
    )
    if phase == "collecting":
        return common + (
            "Collect the COMPLETE question list from the user's text/files or gateway. Follow pagination and "
            "preserve original IDs, names/descriptions, shared user constraints, targets, submission requirements "
            "and explicitly supplied difficulty. Download all required question attachments into /workspace "
            "before registration, or reference supplied /inputs files. Do not invent difficulty or declare a "
            "failed download complete. Use scout_register_questions with source_key,title,description, optional "
            "difficulty,connection_info,submission_instructions,attachments (local /workspace or /inputs paths). "
            "Use original gateway IDs as source_key; for user text use distinct stable source labels. "
            "Registration can be batched and identical retries are deduplicated. Do not solve questions yet. "
            "Only after the entire accessible list and its files are registered call scout_finish_collection "
            "with expected_count equal to the complete source list count. No fixed total collection budget."
        )
    if phase == "executing":
        return common + (
            "FIRST ROUND: only inspect this assigned question's attachments/targets and preserve raw results. "
            "Use the current question working directory for all downloads/scripts/outputs. "
            "Do not call Intel tools or write reports. The runtime journals all executions for this question. "
            "Basic recon is enough; remaining questions also need coverage. Finish this turn when done."
        )
    if phase == "extracting":
        return common + (
            "SECOND ROUND: cover every action of THIS question, including errors and unfinished checks. "
            "Use provided literal materials first; read full files when truncated, missing or binary. "
            "An omitted excerpt is not an empty result or proof a capability is absent. "
            "scout_write_intel(action_id,claim,detail?,topic?) automatically binds THIS question and recorded "
            "result/attachments. Check saved=true. No new probes, evidence modifications or merges. "
            "No total time budget: finish only after every supplied action is covered. "
            "Second/third-round tools are not journaled."
        )
    return common + (
        "THIRD ROUND: consolidate the raw Intel of THIS question only. Never group different questions. "
        "AI chooses semantic groups and writes multi-entry summaries preserving conditions, independent facts, "
        "negative-test scope and later corrections. Use scout_merge_intel(merged_from_ids,claim?,detail?,topic?). "
        "For a singleton give ONLY its original ID; the server copies original text verbatim. "
        "Every raw ID must appear exactly once; retained raw JSON must not change. "
        "No new probes. No total time budget; finish after full coverage. The system imports final Intel "
        "to this question's own board, never a combined batch board."
    )


async def scout() -> None:
    batch_id = os.environ["TGA3_AOE_PARENT_ID"]
    root_workspace = Path(os.environ.get("TGA3_WORKSPACE", "/workspace"))
    state = root_workspace / ".tga3"
    state.mkdir(parents=True, exist_ok=True)
    os.environ["TGA3_CLI_HOME"] = str(root_workspace / ".codex")
    adapter = CodexAdapter()
    adapter.journal.identity["scope"] = "batch_collection"
    phases = ["collecting", "executing", "extracting", "merging"]
    start_phase = os.environ.get("TGA3_SCOUT_START_PHASE", "collecting")
    phases = phases[phases.index(start_phase) :]
    manifest_path = Path("/aoe/inputs/questions.json")

    async def emit(method, params):
        question_id = os.environ.get("TGA3_QUESTION_ID")
        print(
            json.dumps(
                {"method": method, "params": {**params, **({"question_id": question_id} if question_id else {})}},
                ensure_ascii=False,
            ),
            flush=True,
        )

    def manifest():
        return (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {"collection_complete": False, "questions": []}
        )

    def switch(question: dict, phase: str):
        ident = question["question_id"]
        root = Path("/aoe") / ident
        workspace, logs = root / "workspaces/scout", root / "logs/scout"
        workspace.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        adapter.workspace = adapter.output_dir = workspace
        os.environ.update(
            TGA3_TASK_ID=ident,
            TGA3_QUESTION_ID=ident,
            TGA3_WORKSPACE=str(workspace),
            TGA3_INPUTS=str(root / "inputs"),
            TGA3_LOGS=str(logs),
            TGA3_INTELS=str(root / "intels/scout"),
        )
        adapter.journal = ActionJournal(
            logs,
            identity={"task_id": ident, "question_id": ident, "agent_id": "scout", "runtime": adapter.runtime},
            secret=adapter.api_key,
            enabled=phase == "executing",
            output_directory=workspace,
        )

    for index, phase in enumerate(phases):
        if index:
            signal = {
                "executing": "scout-execute-request",
                "extracting": "scout-extract-request",
                "merging": "scout-merge-request",
            }[phase]
            while not (state / signal).exists():
                await asyncio.sleep(0.1)
        os.environ["TGA3_SCOUT_PHASE"] = adapter.scout_phase = phase
        budget = float(os.environ.get("TGA3_SCOUT_TIMEOUT_SECONDS", "300")) if phase == "executing" else None
        adapter.timeout = budget + 5 if budget is not None else None
        result = {
            "task_id": batch_id,
            "phase": phase,
            "finish_reason": "completed",
            "error": None,
            "budget_seconds": budget,
        }
        try:
            async with asyncio.timeout(budget):
                if phase == "collecting":
                    adapter.journal.enabled = True
                    stalled = 0
                    while not manifest()["collection_complete"]:
                        before = len(manifest()["questions"])
                        output = await adapter.run_cycle(instructions(), emit)
                        if output.strip():
                            await emit("agent.output.delta", {"text": output})
                        stalled = stalled + 1 if len(manifest()["questions"]) == before else 0
                        if stalled >= 3 and not manifest()["collection_complete"]:
                            raise RuntimeError(
                                "Scout ended three collection turns without registering more questions; "
                                "the complete source list has not been collected"
                            )
                else:
                    questions = manifest()["questions"]
                    stop_at = asyncio.get_running_loop().time() + budget if budget else None
                    for offset, question in enumerate(questions):
                        switch(question, phase)
                        if phase == "executing":
                            # Divide only the existing first-round budget; extraction/merge remain unbounded.
                            remaining = max(0.1, stop_at - asyncio.get_running_loop().time())
                            try:
                                async with asyncio.timeout(remaining / (len(questions) - offset)):
                                    await adapter.run_cycle(
                                        instructions() + "\nQuestion:\n" + json.dumps(question, ensure_ascii=False),
                                        emit,
                                    )
                            except TimeoutError:
                                continue
                        else:
                            while True:
                                intel_dir = Path(os.environ["TGA3_INTELS"])
                                ident = UUID(question["question_id"])
                                raw = read_intels(intel_dir / "raw", ident)
                                if phase == "extracting":
                                    covered = {
                                        a for item in (raw.intels if raw else []) for a in item.source_action_ids
                                    }
                                    missing = [
                                        a["action_id"]
                                        for a in read_action_log(adapter.journal.directory)["actions"]
                                        if a["action_id"] not in covered
                                    ]
                                    if not missing:
                                        break
                                    materials = action_materials(
                                        adapter.journal.directory, adapter.workspace, ident, missing
                                    )
                                else:
                                    merged = read_intels(intel_dir / "merged", ident, merged=True)
                                    covered = {
                                        a for item in (merged.intels if merged else []) for a in item.merged_from_ids
                                    }
                                    materials = [
                                        {"id": item.id, "topic": item.topic, "body": item.body.model_dump()}
                                        for item in (raw.intels if raw else [])
                                        if item.id not in covered
                                    ]
                                    if not materials:
                                        break
                                await adapter.run_cycle(
                                    instructions()
                                    + "\nQuestion materials:\n"
                                    + json.dumps(materials, ensure_ascii=False),
                                    emit,
                                )
        except TimeoutError:
            result["finish_reason"] = "timeout"
        except Exception as exc:
            result.update(finish_reason="error", error=str(exc).replace(adapter.api_key, "[redacted]"))
        temporary = state / f"scout-{phase}-result.tmp"
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(state / f"scout-{phase}-result.json")
        if phase != "executing" and result["finish_reason"] != "completed":
            return
