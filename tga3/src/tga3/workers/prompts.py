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
    )
    publication_contract = (
        "Blackboard publication contract (authoritative and generated from the runtime models):\n"
        f"{worker_publish_contract()}\n"
        "The blackboard_publish tool accepts one request object. Follow the schema branch selected by request.kind."
    )
    return f"{os.environ['TGA3_SYSTEM_PROMPT'].rstrip()}\n\n{runtime_context}\n\n{publication_contract}"


__all__ = ["worker_instructions"]
