"""Worker system prompts are injected from config/agents.json."""

import json
import os

from ..domain import publication_contract as build_publication_contract


def worker_instructions() -> str:
    role = os.environ.get("TGA3_AGENT_ROLE", "worker")
    actor = {
        "agent_id": os.environ["TGA3_AGENT_ID"],
        "display_name": os.environ["TGA3_AGENT_DISPLAY_NAME"],
        "role": role,
        "sdk": os.environ["TGA3_AGENT_RUNTIME"],
        "model": os.environ["TGA3_MODEL_NAME"],
    }
    runtime_context = (
        "TGA3 runtime identity (authoritative):\n"
        f"- task_id: {os.environ['TGA3_TASK_ID']}\n"
        f"- actor: {json.dumps(actor, ensure_ascii=False, separators=(',', ':'))}\n"
        "For blackboard_publish final_candidate calls, use the exact task_id and actor above. "
        "finding_publish receives your identity from the runtime connection; do not fill task_id or actor. "
        "Never invent a placeholder task ID or actor."
        " Your CLI working directory is /workspace. Your private output directory is /workspace; "
        "The runtime stores structured execution records in /logs. "
        f"Each action in /logs/{actor['agent_id']}-actions.json has artifact.output_path "
        "pointing to its saved result body "
        "and artifact.attachments listing "
        "generated files, all relative to /workspace. Reuse these files as evidence when sufficient. "
        "Use native CLI file/list/search tools "
        "to discover /skills and read each package's SKILL.md, then follow its referenced documents."
    )
    publication_contract = (
        "Blackboard publication contract (authoritative and generated from the runtime models):\n"
        f"{build_publication_contract(role)}\n"
        "Use finding_publish for immediate observations. blackboard_publish accepts one request object "
        "for final_candidate only."
    )
    if role == "scout":
        runtime_context = (
            f"Authoritative task_id: {os.environ['TGA3_TASK_ID']}. "
            "Working directory: /workspace. Inputs: /inputs (read-only). Output files: "
            "/workspace. Execution records: /logs. "
            "Action artifact paths are relative to /workspace; output bodies are saved in /workspace/result/. "
            "Skills: /skills, read SKILL.md only when useful."
        )
        phase = os.environ.get("TGA3_SCOUT_PHASE", "executing")
        if phase == "extracting":
            instructions = (
                "Current phase: SECOND ROUND, Intel extraction in the SAME CLI session and container. "
                "Read the actions array in /logs/scout-actions.json and files in /workspace. "
                "Do not probe targets, rerun reconnaissance, modify evidence or install tools. "
                "Extract verified observations, scoped negative results and clearly labelled unfinished checks. "
                "Summarize EVERY action record, including failed and unfinished actions, into Intel. "
                "Include its unique action_id in source_action_ids so per-action coverage can be verified. "
                "Save each coherent Intel with scout_write_intel using "
                "request={task_id:<exact task UUID>,claim:<fact>,detail:<support>,"
                "topic:<topic>,source_action_ids:[<action_id>],evidence_paths:[<paths relative to /workspace>]}. "
                "No actor, kind, artifact UUID or JSON formatting is needed. evidence_paths may be empty. "
                "Check saved=true; the tool appends to /intels/raw/scout-intels.json without registering evidence. "
                "The receipt contains its stable scout-raw-intel- ID; identical retries return the same ID. "
                "Do not choose a name, key or ID. This original collection is retained. "
                "Do not merge Intel yet. There is no total extraction time budget; finish only after ALL actions "
                "are covered. Then wait for the THIRD work phase in this same session. "
                "Tool calls in the second and third phases are not appended to the action log. "
                "Do not publish to the blackboard."
            )
        elif phase == "merging":
            instructions = (
                "Current phase: THIRD ROUND, Intel consolidation in the SAME CLI session and container. "
                "All action records have been extracted. "
                "Original Intel in /intels/raw/scout-intels.json is retained unchanged. "
                "Use the supplied compact Intel catalog first; read originals/evidence only to resolve differences. "
                "Consolidate repeated or complementary facts about the same object under the same conditions. "
                "Preserve distinct facts, exact targets, inputs, conditions, scoped negatives, unfinished checks "
                "and later corrections. Never erase a successful follow-up or generalize a failed test. "
                "Keep unrelated facts separate; never invent a new conclusion. "
                "Call scout_merge_intel with request={task_id:<exact task UUID>,"
                "claim:<fact>,detail:<support and boundaries>,topic:<topic>,merged_from_ids:[<raw Intel IDs>]}. "
                "Each raw ID must appear exactly once across final Intel; a singleton stays separate. "
                "IDs are assigned by the server; never invent names or IDs. "
                "The server automatically unions action IDs and evidence paths. Do not copy those fields. "
                "Check saved=true; final entries accumulate in /intels/merged/scout-intels.json, "
                "with server-generated scout-merged-intel- IDs distinct from original IDs. "
                "Evidence is registered only when final Intel is imported. "
                "No total time budget: finish only after every raw Intel is represented in final Intel. "
                "Do not call scout_write_intel, modify originals, probe targets or rerun reconnaissance. "
                "Second/third-phase tool calls are not journaled. Only merged Intel is imported into the blackboard "
                "by the system after complete coverage; do not publish yourself."
            )
        else:
            instructions = (
                "Current phase: FIRST ROUND, reconnaissance execution only. "
                "Use native tools to inspect inputs and the target, and preserve reusable raw "
                "files in /workspace/. "
                "Do not summarize Intel, call Intel tools, publish to the blackboard or write a recon report. "
                "Tool calls and their output are automatically saved as records in /logs/scout-actions.json; "
                "do not modify those reserved records. Save downloads, extracted files and "
                "scripts under /workspace/. "
                "Use /workspace for scratch work. Later extract and then merge Intel in this same session. "
                "Track time and limit individual commands; basic reconnaissance is enough, then finish. "
                f"Execution budget: {os.environ.get('TGA3_SCOUT_TIMEOUT_SECONDS', '300')} seconds."
            )
        return (f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{instructions}\n"
                "Native hooks provide the task, blackboard and realtime user prompts at tool boundaries. "
                "User prompts from actor.role=user are direct user instructions. "
                "Blackboard reading is supplied by native hooks.")
    publication_contract += (
        "\nThe native CLI hooks deliver the initial complete blackboard and subsequent unseen entries automatically. "
        "Do not reread the full blackboard yourself. At the next tool opportunity, "
        "updates are supplied only when the configured interval elapsed and new entries exist. "
        "Before final_candidate, the hook bypasses the interval; review newly supplied entries and retry if blocked. "
        "Continue working without waiting for updates. Tool hooks handle cursors and elapsed time."
    )
    finding_example = {"action_id": f"{actor['agent_id']}-action-<ID supplied with the tool result>",
                       "claim": "<verified reusable observation>",
                       "scope": "<tested target, inputs, conditions and limits>"}
    final_candidate_example = {
        "request": {
            "kind": "final_candidate",
            "task_id": os.environ["TGA3_TASK_ID"],
            "actor": actor,
            "topic": "<answer topic>",
            "body": {
                "conclusion": "<verified final answer>",
                "rationale": "<why the referenced Findings support this answer>",
                "answer_type": "flag",
                "finding_ids": ["<entry.id returned by finding_publish>"],
            },
            "idempotency_key": "<unique key for this final candidate>",
        },
    }
    publication_contract += (
        "\nUse finding_publish with these arguments (no request wrapper):\n"
        + json.dumps(finding_example, ensure_ascii=False)
        + "\nAfter EACH execution, use the result already in this conversation. If it establishes a reusable fact, "
        "publish it immediately before the next investigation step. One execution is enough; "
        "do not wait for a validation batch, decisive exploit or final answer. "
        "Share both what exists/works and what failed under the tested conditions. "
        "Do not schedule log reviews or reread logs to extract Findings. Ordinary navigation, duplicate facts, "
        "plans and guesses need no publication. Tool errors/timeouts do not rule out a target capability. "
        "The post-tool hook supplies the exact action_id; never substitute a native tool-call ID. "
        "The server resolves task/actor, command/input, execution status, saved output and attachments, "
        "registers evidence and generates an idempotent retry key. "
        "Use optional preconditions only for prerequisites not present in the recorded command. "
        "Check saved=true and entry.id. If publication fails, retain the fact, correct the reported issue "
        "and retry with the same action_id, claim and scope before further investigation. "
        "A transport retry with identical arguments cannot create another entry. "
        "If the result adds no reusable fact, continue normally without an explanation or status entry. "
        "\nFinal candidate uses blackboard_publish with this request object:\n"
        + json.dumps(final_candidate_example, ensure_ascii=False)
        + "\nFor final_candidate, reference published Finding entry UUIDs in finding_ids, omit evidence_paths, "
        "and provide a nonempty idempotency_key; reuse it on retries. "
        "Publication is complete only after blackboard_publish returns the saved entry. "
        "The system imports Scout Intel after extraction and merging; "
        "consume it through the existing blackboard delivery. "
    )
    return f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{publication_contract}"


__all__ = ["worker_instructions"]
