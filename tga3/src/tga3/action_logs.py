"""Agent-level action manifests and stable action provenance."""

import json
import re
from pathlib import Path

from .errors import ContractError


def action_log_name(agent_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", agent_id):
        raise ContractError("Invalid action log agent prefix")
    return f"{agent_id}-actions.json"


def read_action_log(directory: Path, *, task_id=None, agent_id=None) -> dict:
    if agent_id is not None:
        path = directory / action_log_name(agent_id)
    else:
        candidates = list(directory.glob("*-actions.json"))
        if len(candidates) > 1:
            raise ContractError("Expected one manifest in the agent's log directory")
        path = candidates[0] if candidates else directory / "agent-actions.json"
    if not path.exists():
        return {"schema_version": 2, "task_id": str(task_id) if task_id else None,
                "agent_id": agent_id, "actions": []}
    if path.is_symlink():
        raise ContractError("Action manifest must be a regular file")
    document = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(document, dict) or document.get("schema_version") != 2
            or not isinstance(document.get("actions"), list)):
        raise ContractError("Invalid agent action manifest")
    if task_id is not None and document.get("task_id") != str(task_id):
        raise ContractError("Action manifest does not belong to this task")
    if agent_id is not None and document.get("agent_id") != agent_id:
        raise ContractError("Action manifest does not belong to this agent")
    seen = set()
    for action in document["actions"]:
        action_id = action.get("action_id") if isinstance(action, dict) else None
        if not isinstance(action_id, str) or not action_id or action_id in seen:
            raise ContractError("Action IDs must be nonempty and unique")
        if document.get("agent_id") and not action_id.startswith(f"{document['agent_id']}-action-"):
            raise ContractError("Action ID prefix does not match its agent")
        seen.add(action_id)
        artifact = action.get("artifact", {})
        if (not isinstance(artifact, dict) or artifact.get("output_path") is not None
                and not isinstance(artifact["output_path"], str)
                or not isinstance(artifact.get("attachments", []), list)
                or not all(isinstance(path, str) for path in artifact.get("attachments", []))):
            raise ContractError("Invalid action artifact description")
    return document


def resolve_action_sources(
    directory: Path, task_id, agent_id: str, requested: list[str], paths: list[str],
) -> list[str]:
    actions = read_action_log(directory, task_id=task_id, agent_id=agent_id)["actions"]
    known = {action["action_id"] for action in actions}
    if set(requested) - known:
        raise ContractError("source_action_ids contains an unknown action for this agent")
    if requested:
        return list(dict.fromkeys(requested))
    evidence = {Path(path).as_posix().removeprefix("./") for path in paths}
    return [action["action_id"] for action in actions
            if evidence.intersection([action.get("artifact", {}).get("output_path"),
                                      *action.get("artifact", {}).get("attachments", [])])]
