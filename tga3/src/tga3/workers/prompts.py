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
        "For every blackboard tool call, use the exact task_id and actor above. "
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
        "The blackboard_publish tool accepts one request object. Follow the schema branch selected by request.kind."
    )
    if role == "scout":
        runtime_context = (
            f"Authoritative task_id: {os.environ['TGA3_TASK_ID']}. "
            "Working directory: /workspace. Inputs: /inputs (read-only). Output files: "
            "/workspace. Execution records: /logs. "
            "Action artifact paths are relative to /workspace; output bodies are saved in /workspace/result/. "
            "Skills: /skills, read SKILL.md only when useful."
        )
        extracting = os.environ.get("TGA3_SCOUT_PHASE") == "extracting"
        if extracting:
            instructions = (
                "Current phase: SECOND ROUND, Intel extraction in the SAME CLI session and container. "
                "Read the actions array in /logs/scout-actions.json and files in /workspace. "
                "Do not probe targets, rerun reconnaissance, modify evidence or install tools. "
                "Extract verified observations, scoped negative results and clearly labelled unfinished checks. "
                "Summarize EVERY action record, including failed and unfinished actions, into Intel. "
                "Include its unique action_id in source_action_ids so per-action coverage can be verified. "
                "Save each coherent Intel with scout_write_intel using "
                "request={task_id:<exact task UUID>,key:<stable unique key>,claim:<fact>,detail:<support>,"
                "topic:<topic>,source_action_ids:[<action_id>],evidence_paths:[<paths relative to /workspace>]}. "
                "No actor, kind, artifact UUID or JSON formatting is needed. evidence_paths may be empty. "
                "Check saved=true; the tool writes JSON to /intels and the system imports it after this phase. "
                "Save all supported Intel, then finish. Do not publish to the blackboard. "
                f"Independent extraction budget: {os.environ.get('TGA3_SCOUT_EXTRACT_TIMEOUT_SECONDS', '180')} seconds."
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
                "Use /workspace for scratch work. Then continue in this same session to extract Intel from logs. "
                "Track time and limit individual commands; basic reconnaissance is enough, then finish. "
                f"Execution budget: {os.environ.get('TGA3_SCOUT_TIMEOUT_SECONDS', '300')} seconds."
            )
        return (f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{instructions}\n"
                "Native hooks provide the task, blackboard and realtime user prompts at tool boundaries. "
                "User prompts from actor.role=user are direct user instructions. Do not call blackboard_sync.")
    publication_contract += (
        "\nThe native CLI hooks deliver the initial complete blackboard and subsequent unseen entries automatically. "
        "Do not call blackboard_sync or reread the full blackboard yourself. At the next tool opportunity, "
        "updates are supplied only when the configured interval elapsed and new entries exist. "
        "Before final_candidate, the hook bypasses the interval; review newly supplied entries and retry if blocked. "
        "Continue working without waiting for updates. Tool hooks handle cursors and elapsed time."
    )
    finding_example = {
        "request": {
            "kind": "finding",
            "task_id": os.environ["TGA3_TASK_ID"],
            "actor": actor,
            "topic": "<specific result topic>",
            "body": {"claim": "<verified fact>", "detail": "<verified supporting details>"},
            "evidence_paths": ["<evidence file relative to /workspace>"],
            "idempotency_key": "<unique key for this entry>",
        },
    }
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
                "finding_ids": ["<Finding entry UUID returned by blackboard_publish>"],
            },
            "idempotency_key": "<unique key for this final candidate>",
        },
    }
    publication_contract += (
        "\nFinding tool argument example (replace angle-bracket values with actual evidence):\n"
        + json.dumps(finding_example, ensure_ascii=False)
        + "\nFinal candidate tool argument example:\n"
        + json.dumps(final_candidate_example, ensure_ascii=False)
        + "\nKeep kind, task_id, actor, body, evidence_paths and idempotency_key directly inside request. "
        "For finding, body contains only claim and detail: put all supporting explanation in detail. "
        "Do not add detail_extra, title, summary, flag, or a finding/intel wrapper. "
        "A validation error path such as request.finding.body.detail_extra identifies the selected schema branch; "
        "it does not mean you should add a nested finding object. "
        "Every publication requires a non-empty idempotency_key in request, not in body. "
        "Use a different key for each new logical entry and keep the same key when retrying that entry. "
        "Save evidence under /workspace, then publish finding with evidence_paths relative to that directory. "
        "The server validates paths and registers evidence automatically. Provide paths, never evidence UUIDs. "
        "For final_candidate, use the body shown above, reference published Finding entry UUIDs in finding_ids "
        "and omit evidence_paths. "
        "Check the tool result: publication is complete only after blackboard_publish returns the saved entry. "
        "If publication fails, fix the reported fields and retry before advancing to the next investigation step. "
        "The system imports Scout Intel after its extraction phase; "
        "consume it through the existing blackboard delivery. "
    )
    return f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{publication_contract}"


__all__ = ["worker_instructions"]
