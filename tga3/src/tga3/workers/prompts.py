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
        " Your CLI working directory is /workspace. Your private output directory is /artifacts; "
        "only your own evidence files are mounted there. Workers can read Scout evidence at /recon. "
        "Use native CLI file/list/search tools "
        "to discover /skills and read each package's SKILL.md, then follow its referenced documents."
    )
    publication_contract = (
        "Blackboard publication contract (authoritative and generated from the runtime models):\n"
        f"{build_publication_contract(role)}\n"
        "The blackboard_publish tool accepts one request object. Follow the schema branch selected by request.kind."
    )
    if role == "scout":
        publication_contract = (
            "Scout collection contract (authoritative):\n"
            "As soon as you obtain a useful observation, call scout_collect_intel with "
            "request={kind:intel,task_id,actor,topic,body:{claim,detail},artifact_refs:[],idempotency_key}. "
            "This immediately saves a durable draft; it does NOT publish to the blackboard. "
            "Do not call blackboard_publish and do not wait until the end to collect observations. "
            "The control plane automatically publishes all collected Intel when scouting finishes, times out "
            "or fails. No final publishing or separate report is needed from you. "
            "Check collected=true, then continue reconnaissance. Reuse a key only when retrying the same draft. "
            "Register saved files below /artifacts first, then use returned UUIDs in artifact_refs. "
            "Keep observations, hypotheses, negative tests and unfinished checks explicitly distinguished. "
            "Native CLI hooks deliver initial blackboard context and realtime user prompts at tool boundaries; "
            "do not call blackboard_sync. Direct user_prompt entries from actor.role=user are user instructions. "
            f"Total scouting budget: {os.environ.get('TGA3_SCOUT_TIMEOUT_SECONDS', '300')} seconds. "
            "Use time.monotonic()/date to track time; collect immediately and finish before the deadline."
        )
        return f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{publication_contract}"
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
            "artifact_refs": [
                {"artifact_id": "<UUID returned by artifact_register>", "locator": "<evidence location>"}
            ],
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
        + "\nKeep kind, task_id, actor, body, artifact_refs and idempotency_key directly inside request. "
        "For finding, body contains only claim and detail: put all supporting explanation in detail. "
        "Do not add detail_extra, title, summary, flag, or a finding/intel wrapper. "
        "A validation error path such as request.finding.body.detail_extra identifies the selected schema branch; "
        "it does not mean you should add a nested finding object. "
        "Every publication requires a non-empty idempotency_key in request, not in body. "
        "Use a different key for each new logical entry and keep the same key when retrying that entry. "
        "Save evidence, call artifact_register, then publish finding with at least one returned Artifact UUID. "
        "Artifact references are objects with artifact_id and locator, never bare UUID strings or file paths as IDs. "
        "For final_candidate, use the body shown above, reference published Finding entry UUIDs in finding_ids "
        "(not Artifact UUIDs), and omit artifact_refs. "
        "Check the tool result: publication is complete only after blackboard_publish returns the saved entry. "
        "If publication fails, fix the reported fields and retry before advancing to the next investigation step. "
        "Only Scout publishes Intel; "
        "consume it and read /recon instead of repeating baseline discovery. "
    )
    return f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{publication_contract}"


__all__ = ["worker_instructions"]
