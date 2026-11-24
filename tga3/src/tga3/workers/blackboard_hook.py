"""Identical native Hook protocol for Codex and Claude; no model tool call required."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

from ..flag_signals import file_flag_format, flag_pattern
from .action_hook import record_tool, result_context
from .aoe_access import overview_policy, scout_post_tool_notice

_publication_versions = {}


def submission_failure(event, record):
    if not record or not os.environ.get('TGA3_AOE_PARENT_ID') or os.environ.get('TGA3_AOE_AUTO_SUBMIT_FLAG') == '0':
        return False
    from ..submissions import SubmissionSpec, is_rejected_value, receipt_value, response_objects, result_text

    path = Path(os.environ.get('TGA3_WORKSPACE', '/workspace')) / '.tga3/submission.json'
    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding='utf-8')).get('submission')
        if not value:
            return False
        spec = SubmissionSpec.model_validate(value)
    except (OSError, ValueError):
        return False
    command = record.get('action', {}).get('command', '')
    if spec.request.url not in command:
        return False
    response = event.get('tool_response', '')
    text = result_text(response if isinstance(response, str) else json.dumps(response))
    return any(
        is_rejected_value(receipt_value(r, spec.success_path), spec.success_value)
        and not (spec.duplicate_code and r.get("code") == spec.duplicate_code)
        for r in response_objects(text)
    )


def outbox_pending() -> bool:
    if os.environ.get("TGA3_AGENT_ROLE") != "scout" or not os.environ.get("TGA3_AOE_PARENT_ID"):
        return False
    outbox = Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / "outbox"
    if not outbox.is_dir():
        return False
    return any(
        not p.name.startswith(".") and p.suffix.lower() == ".json" and not p.name.endswith(".tmp")
        for p in outbox.iterdir()
    )


def publications_changed():
    workspace = Path(os.environ.get('TGA3_WORKSPACE', '/workspace'))
    root = Path(os.environ.get('TGA3_FINDINGS', '/findings'))
    paths = [*root.glob('*.json')]
    final = workspace / 'final-candidate.json'
    if final.is_file():
        paths.append(final)
    version = tuple(sorted((str(p), p.stat().st_mtime_ns, p.stat().st_ctime_ns, p.stat().st_size) for p in paths))
    key = (str(workspace), str(root))
    changed = bool(version) and _publication_versions.get(key) != version
    _publication_versions[key] = version
    return changed


def worker_finding_nudge(record: dict | None, *, imported_finding: bool = False) -> str:
    """Track tool cadence since the last Finding and emit low-noise milestone/handoff nudges."""
    if not record or os.environ.get("TGA3_AGENT_ROLE") != "worker":
        return ""
    tool = str(record.get("action", {}).get("tool", "")).lower()
    if "blackboard" in tool:
        return ""
    workspace = Path(os.environ.get("TGA3_WORKSPACE", "/workspace"))
    state_dir = workspace / ".tga3"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "worker-finding-state.json"
    scope = os.environ.get("TGA3_ATTEMPT_ID") or os.environ.get("TGA3_TASK_ID") or "task"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    except (OSError, ValueError):
        state = {}
    if state.get("scope") != scope:
        state = {"scope": scope, "steps_since_finding": 0, "known_findings": 0, "handoff_reminded": False}

    findings_root = Path(os.environ.get("TGA3_FINDINGS", "/findings"))
    current_findings = (
        sum(1 for p in findings_root.glob("*.json") if not p.name.startswith("."))
        if findings_root.is_dir()
        else 0
    )
    if imported_finding or current_findings > int(state.get("known_findings", 0)):
        state["steps_since_finding"] = 0
        state["known_findings"] = current_findings
    else:
        state["steps_since_finding"] = int(state.get("steps_since_finding", 0)) + 1

    nudges: list[str] = []
    try:
        interval = int(os.environ.get("TGA3_FINDING_NUDGE_STEPS", "6"))
    except ValueError:
        interval = 6
    steps = int(state["steps_since_finding"])
    if interval > 0 and steps > 0 and steps % interval == 0:
        nudges.append(
            f"[阶段沉淀提醒] 你已连续执行 {steps} 步工具调用但尚未写入新的 Finding。"
            "若已确认以下任一进展，请现在用 1 条简短 JSON 写入 /findings/<简短名>.json"
            '（仅需 {"claim": "..."}，系统自动关联最近 action 与证据）：\n'
            "1) 架构/防护/源码/关键接口或协议细节；2) 已验证的漏洞原语/有效载荷/关键线索；"
            "3) 已明确证伪的死胡同（帮队友避坑）；4) /workspace 中已写好的关键脚本或中间产物。"
        )

    deadline_raw = os.environ.get("TGA3_ATTEMPT_DEADLINE", "").strip()
    if (
        deadline_raw
        and os.environ.get("TGA3_AOE_PARENT_ID")
        and not state.get("handoff_reminded")
        and not (workspace / "final-candidate.json").is_file()
    ):
        try:
            remaining = float(deadline_raw) - time.time()
            threshold = float(os.environ.get("TGA3_FINDING_HANDOFF_SECONDS", "150"))
        except ValueError:
            remaining, threshold = -1.0, 150.0
        if 0 < remaining <= threshold:
            state["handoff_reminded"] = True
            nudges.append(
                f"[交棒沉淀提醒] 本轮剩余约 {int(remaining)} 秒且尚未产出最终候选。"
                "请尽快将目前已验证的关键线索、已排除的无效方向、以及 /workspace 中可复用的脚本/中间产物"
                '整理为 1-2 条 Finding 写入 /findings/（只需 {"claim": "...", "scope": "..."}），'
                "避免超时交棒后后续队友从零重复踩坑。"
            )

    tmp = state_path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(state_path)
    finally:
        tmp.unlink(missing_ok=True)
    return "\n\n".join(nudges)


def checkpoint(event: dict, *, client=None, record_action=None) -> dict:
    if os.environ.get("TGA3_AGENT_ROLE") not in {"scout", "worker"}:
        return {}
    event_name = event.get("hook_event_name", "PreToolUse")
    if reason := overview_policy(event):
        return {'hookSpecificOutput': {'hookEventName': event_name, 'permissionDecision': 'deny',
                                      'permissionDecisionReason': reason}}
    post_tool = event_name in {"PostToolUse", "PostToolUseFailure"}
    record_action = record_action or record_tool
    record = record_action(event) if post_tool else None
    failed_submission = submission_failure(event, record) if post_tool else False
    deadline = os.environ.get('TGA3_ATTEMPT_DEADLINE')
    completion_hint = False
    if (post_tool and record and deadline and float(deadline) - time.time() <= 30
            and os.environ.get("TGA3_AOE_PARENT_ID")):
        workspace = Path(os.environ.get("TGA3_WORKSPACE", "/workspace"))
        flag_format = file_flag_format(workspace / ".tga3/submission.json")
        if not flag_format:
            flag_format = file_flag_format(os.environ.get("TGA3_QUESTION_SOURCE", "/question-source.json"))
        completion_hint = bool(flag_pattern(flag_format).search(str(event.get("tool_response", ""))))
    scout_outbox = post_tool and outbox_pending()
    if (
        post_tool
        and not publications_changed()
        and not completion_hint
        and not failed_submission
        and not scout_outbox
    ):
        parts = []
        if record:
            parts.append(result_context(record))
            if nudge := worker_finding_nudge(record):
                parts.append(nudge)
        if scout_notice := scout_post_tool_notice(event):
            parts.append(scout_notice)
        return ({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": "\n\n".join(parts)}}
                if parts else {})
    state_dir = Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / ".tga3"
    state_dir.mkdir(parents=True, exist_ok=True)
    ack_file = state_dir / "blackboard-ack.json"
    generation = event.get("_tga_generation", os.environ.get("TGA3_CLI_GENERATION", ""))
    previous = json.loads(ack_file.read_text()) if ack_file.is_file() else {}
    arguments = event.get("tool_input", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}

    def candidate(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return False
        if not isinstance(value, dict):
            return False
        return value.get("kind") == "final_candidate" or any(
            candidate(value.get(key)) for key in ("request", "arguments", "input")
        )

    final = (str(event.get("tool_name", "")).split("__")[-1] == "blackboard_publish"
             or candidate(arguments))
    start_token = os.environ.get("TGA3_BOARD_START_TOKEN", "")
    experience_generation = os.environ.get("TGA3_EXPERIENCE_SESSION_TOKEN") or start_token or "task-context"
    experience_ack_file = state_dir / "experience-ack.json"
    experience_previous = json.loads(experience_ack_file.read_text()) if experience_ack_file.is_file() else {}
    initial_file = state_dir / "experience-initial-catalog.json"
    initial_ack_file = state_dir / "experience-initial-ack"
    initial = json.loads(initial_file.read_text()) if initial_file.is_file() else {}
    send_initial = (initial.get("generation") == experience_generation and initial.get("key")
                    and (not initial_ack_file.is_file() or initial_ack_file.read_text() != initial["key"]))
    start_file = state_dir / "aoe-start-token"
    force_start = bool(
        start_token and event_name in {"UserPromptSubmit", "PreToolUse"}
        and (not start_file.is_file() or start_file.read_text() != start_token)
    )
    base = os.environ["TGA3_BLACKBOARD_MCP_URL"].rstrip("/").removesuffix("/mcp")
    response = (client.post if client is not None else httpx.post)(
        f"{base}/internal/blackboard/{os.environ['TGA3_TASK_ID']}/{os.environ['TGA3_AGENT_ID']}/checkpoint",
        json={
            "ack": (previous.get("delivery_id", "")
                    if not post_tool and previous.get("generation") == generation else ""),
            "final": final or force_start,
            "ack_only": bool(event.get("ack_only")),
            "initial_only": event.get("hook_event_name") == "UserPromptSubmit" and not force_start,
            "file_sync": os.environ.get("TGA3_AGENT_ROLE") == "worker",
            "outbox_sync": scout_outbox,
            "action_start": (os.environ.get("TGA3_ATTEMPT_ID", "")
                             if event_name == "PreToolUse" and event.get("tool_use_id") else ""),
            "force_start": force_start,
            "defer_context": bool(event.get('_tga_defer_context')),
            "completion_hint": completion_hint,
            **({'submission_failure_action': record['action_id']} if failed_submission else {}),
            "experience_generation": experience_generation,
            "experience_ack": (experience_previous.get("delivery_id", "")
                               if experience_previous.get("generation") == experience_generation else ""),
            **({"experience_initial": initial["fingerprints"]} if send_initial else {}),
        },
        timeout=10,
    )
    response.raise_for_status()
    delivery = response.json()
    if send_initial:
        initial_ack_file.write_text(initial["key"])
    if force_start:
        start_file.write_text(start_token)
    context = delivery.get("context", "")
    defer_context = (event_name == 'PreToolUse' and event.get('_tga_defer_context')
                     and any(delivery.get(k) for k in ['context', 'experience_context']))
    if event_name == "PreToolUse" and not ((final or force_start) and context) and not defer_context:
        record_action(event)
    if context:
        ack_file.write_text(json.dumps({"generation": generation, "delivery_id": delivery["delivery_id"]}))
    parts = []
    if advice := delivery.get("experience_context"):
        parts.append(advice)
        from .experience_catalog import record_catalog

        record_catalog(delivery.get("experience_fingerprints", []))
        experience_ack_file.write_text(json.dumps({"generation": experience_generation,
            "delivery_id": delivery["experience_delivery_id"]}))
    if record:
        parts.append(result_context(record))
        imported_finding = any(
            isinstance(item, dict)
            and str(item.get("path", "")).startswith("/findings/")
            and item.get("status") == "imported"
            for item in (delivery.get("publication_receipts") or [])
        )
        if nudge := worker_finding_nudge(record, imported_finding=imported_finding):
            parts.append(nudge)
    if context:
        parts.append(context)
    if receipts := delivery.get("publication_receipts"):
        parts.append("TGA system file-import receipts: " + json.dumps(receipts, ensure_ascii=False))
    synced_seq = 0
    if import_receipts := delivery.get("question_import_receipts"):
        if isinstance(import_receipts, dict):
            synced_seq = int(import_receipts.get("import_seq", 0) or 0)
        parts.append("TGA system question-import receipts: " + json.dumps(import_receipts, ensure_ascii=False))
    if post_tool:
        if scout_notice := scout_post_tool_notice(event, synced_import_seq=synced_seq):
            parts.append(scout_notice)
    if protection := delivery.get('submission_protection'):
        parts.append('TGA system submission protection: ' + json.dumps(protection, ensure_ascii=False))
    if not parts:
        return {}
    result = {
        "hookSpecificOutput": {
            "hookEventName": event.get("hook_event_name", "PreToolUse"),
            "additionalContext": "\n\n".join(parts),
        }
    }
    if defer_context or context and (final or force_start and event_name == "PreToolUse"):
        result["hookSpecificOutput"].update(
            permissionDecision="deny",
            permissionDecisionReason=(
                "New blackboard updates were delivered. Review them, then resubmit the candidate."
                if final else "Question blackboard updates were delivered. Review them, then retry."
            ),
        )
    return result


def main() -> None:
    try:
        print(json.dumps(checkpoint(json.load(sys.stdin)), ensure_ascii=False))
    except Exception as exc:
        # Do not silently allow a final candidate past a failed freshness check.
        print(f"Blackboard checkpoint failed: {type(exc).__name__}; retry this tool after recovery.", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
