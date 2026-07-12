"""LLM-assisted planner constrained by TGA's safe intent set."""

from __future__ import annotations

import json

from tga.contracts import Intent, TGATask
from tga.models.base import ModelMessage
from tga.models.bootstrap import build_model_client_from_env


def reorder_with_llm(task: TGATask, intents: list[Intent]) -> tuple[list[Intent], dict]:
    client = build_model_client_from_env()
    if client is None:
        return intents, {"enabled": False, "reason": "LLM_NOT_CONFIGURED"}
    allowed = [
        {
            "kind": intent.kind,
            "intent_id": intent.id,
            "goal": intent.goal,
            "risk": intent.risk,
            "tools": intent.required_tools,
        }
        for intent in intents
    ]
    prompt = (
        "你是 TGA 的授权 CTF/安全审查任务规划器。"
        "只能在给定 intent 中重排顺序，不能新增越权动作，不能删除 report。"
        "只输出 JSON，格式为 {\"order\":[\"intent_id\"],\"notes\":\"...\"}。\n"
        f"任务模式: {task.mode}\n"
        f"目标: {task.target}\n"
        f"授权范围: {task.scope}\n"
        f"目标说明: {task.goal}\n"
        f"可选 intent: {json.dumps(allowed, ensure_ascii=False)}"
    )
    try:
        response = client.chat([ModelMessage(role="user", content=prompt)], temperature=0.1)
        payload = json.loads(_json_object(response.content))
    except Exception as exc:  # noqa: BLE001
        return intents, {"enabled": True, "used": False, "reason": f"LLM_PLAN_FAILED: {exc}"}

    order = payload.get("order") if isinstance(payload, dict) else None
    if not isinstance(order, list):
        return intents, {"enabled": True, "used": False, "reason": "LLM_PLAN_INVALID"}

    by_id = {intent.id: intent for intent in intents}
    reordered = [by_id[item] for item in order if item in by_id]
    for intent in intents:
        if intent not in reordered:
            reordered.append(intent)
    if not any(intent.kind == "report" for intent in reordered):
        reordered.extend(intent for intent in intents if intent.kind == "report")
    return reordered, {"enabled": True, "used": True, "notes": str(payload.get("notes") or "")}


def _json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no json object")
    return text[start : end + 1]
