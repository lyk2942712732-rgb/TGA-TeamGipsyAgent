"""Normalize tool calls and block proven dead repetitions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.state import Blackboard

CORE_TOOL_ERROR = "[CORE_TOOL_ERROR]"
CORE_GUARD_BLOCKED = "[CORE_GUARD_BLOCKED]"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted((_jsonable(item) for item in value), key=repr)
    return repr(value)


def normalize_args(args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {"value": _jsonable(args)}
    return _jsonable(args)


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:20]


def call_fingerprint(tool_name: str, args: Any) -> str:
    normalized = normalize_args(args)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _digest(f"{tool_name}:{payload}")


def result_fingerprint(result: str) -> str:
    return _digest(" ".join(result.split()))


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


class ToolGuard:
    def __init__(self, repeat_failure_threshold: int) -> None:
        self.repeat_failure_threshold = repeat_failure_threshold

    def inspect(self, board: Blackboard, tool_name: str, args: Any) -> GuardDecision:
        fingerprint = call_fingerprint(tool_name, args)
        if (
            board.failure_streak >= self.repeat_failure_threshold
            and board.failed_attempts
            and board.failed_attempts[-1].call_fingerprint == fingerprint
        ):
            return GuardDecision(
                allowed=False,
                reason=(
                    f"工具 {tool_name} 使用相同参数已经连续产生 "
                    f"{board.failure_streak} 次相同错误；必须换参数、工具或攻击路径。"
                ),
            )
        return GuardDecision(allowed=True)
