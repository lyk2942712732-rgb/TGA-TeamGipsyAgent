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
        publication_contract += (
            "\nPublish Intel incrementally with request={kind:intel,task_id,actor,topic,body:{claim,detail},"
            "artifact_refs:[],idempotency_key}. Register saved files first, then use returned UUIDs in references. "
            "Do not produce a separate recon report; Intel is the handoff. "
            f"Total scouting budget: {os.environ.get('TGA3_SCOUT_TIMEOUT_SECONDS', '300')} seconds. "
            "Use time.monotonic()/date to track time; publish immediately and finish before the deadline."
        )
        return f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{publication_contract}"
    finding_example = {
        "request": {
            "kind": "finding",
            "task_id": os.environ["TGA3_TASK_ID"],
            "actor": actor,
            "body": {"claim": "<verified fact>", "detail": "<verified supporting details>"},
            "artifact_refs": [
                {"artifact_id": "<UUID returned by artifact_register>", "locator": "<evidence location>"}
            ],
            "idempotency_key": "<unique key for this entry>",
        },
    }
    publication_contract += (
        "\nFinding tool argument example (replace angle-bracket values with actual evidence):\n"
        + json.dumps(finding_example, ensure_ascii=False)
        + "\nKeep kind, task_id, actor, body, artifact_refs and idempotency_key directly inside request. "
        "Do not add a finding/intel wrapper. Artifact references are objects with artifact_id and locator, "
        "never bare UUID strings. Only Scout publishes Intel; "
        "consume it and read /recon instead of repeating baseline discovery. "
        "For final_candidate use the same flat request with its required body.finding_ids and omit artifact_refs."
    )
    return f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{publication_contract}"


__all__ = ["worker_instructions"]
