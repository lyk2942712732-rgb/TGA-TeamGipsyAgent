"""Evidence-driven adaptation for follow-up intents."""

from __future__ import annotations

import json

from tga.contracts import Intent, TGATask
from tga.models.base import ModelMessage
from tga.models.bootstrap import build_model_client_from_env


def refine_intent_with_evidence(task: TGATask, intent: Intent, snapshot: dict) -> tuple[Intent, dict]:
    """Let the LLM refine a safe, already-planned intent using stored evidence.

    The model does not get arbitrary tool execution. It can only rewrite the
    goal of an intent that the scheduler will still run through policy gates.
    """
    if intent.kind not in {"verify", "exploit_ctf"}:
        return intent, {"enabled": False, "reason": "intent_not_adaptive"}
    observations = _observation_digest(snapshot)
    if not observations:
        return intent, {"enabled": False, "reason": "no_prior_evidence"}
    client = build_model_client_from_env()
    if client is None:
        return intent, {"enabled": False, "reason": "LLM_NOT_CONFIGURED", "observations": observations}

    prompt = (
        "你是 TGA 的授权 CTF/安全审查策略模块。你会读取已经产生的真实证据，"
        "只允许改写下一步 intent 的 goal，不允许扩大授权范围，不允许声称已经拿到 flag。"
        "只输出 JSON，格式为 {\"goal\":\"...\",\"rationale\":\"...\"}。\n"
        f"任务模式: {task.mode}\n"
        f"目标: {task.target}\n"
        f"授权范围: {task.scope}\n"
        f"原始 goal: {intent.goal}\n"
        f"证据摘要: {json.dumps(observations, ensure_ascii=False)}"
    )
    try:
        response = client.chat([ModelMessage(role="user", content=prompt)], temperature=0.1)
        payload = json.loads(_json_object(response.content))
    except Exception as exc:  # noqa: BLE001
        return intent, {"enabled": True, "used": False, "reason": f"LLM_ADAPT_FAILED: {exc}", "observations": observations}

    goal = str(payload.get("goal") or "").strip()
    if not goal:
        return intent, {"enabled": True, "used": False, "reason": "LLM_ADAPT_INVALID", "observations": observations}
    refined = intent.model_copy(update={"goal": goal[:800]})
    return refined, {
        "enabled": True,
        "used": True,
        "rationale": str(payload.get("rationale") or ""),
        "observations": observations,
    }


def _observation_digest(snapshot: dict) -> list[dict]:
    digest = []
    for event in snapshot.get("events") or []:
        if event.get("type") != "WORKER_OBSERVATION":
            continue
        payload = event.get("payload") or {}
        digest.append(
            {
                "intent_id": event.get("intent_id"),
                "facts": payload.get("facts") or [],
                "leads": payload.get("leads") or [],
                "errors": payload.get("errors") or [],
                "artifact_excerpts": payload.get("artifact_excerpts") or [],
            }
        )
    return digest[-6:]


def _json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no json object")
    return text[start : end + 1]
