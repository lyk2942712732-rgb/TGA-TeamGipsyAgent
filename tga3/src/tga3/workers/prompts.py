"""Worker system prompts are injected from config/agents.json."""

import json
import os

from ..domain import worker_publish_contract


def worker_instructions() -> str:
    actor = {
        "agent_id": os.environ["TGA3_AGENT_ID"],
        "display_name": os.environ["TGA3_AGENT_DISPLAY_NAME"],
        "role": "worker",
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
        "only your own evidence files are mounted there. Use native CLI file/list/search tools "
        "to discover /skills and read each package's SKILL.md, then follow its referenced documents."
    )
    publication_contract = (
        "Blackboard publication contract (authoritative and generated from the runtime models):\n"
        f"{worker_publish_contract()}\n"
        "The blackboard_publish tool accepts one request object. Follow the schema branch selected by request.kind."
    )
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
        "never bare UUID strings. For Intel use the same flat request with kind=intel and omit artifact_refs. "
        "For final_candidate use the same flat request with its required body.finding_ids and omit artifact_refs."
    )
    return f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{publication_contract}"


__all__ = ["worker_instructions"]
