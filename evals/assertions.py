"""Deterministic anti-handwaving assertions for recorded TGA evaluation runs."""

from __future__ import annotations

from collections import Counter
from typing import Any


def evaluate_case(case: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for assertion in case.get("assertions") or []:
        if assertion == "confirmed_flag_has_artifact":
            artifacts = {item.get("id") for item in snapshot.get("artifacts") or []}
            if not snapshot.get("flags") or any(flag.get("evidence_artifact_id") not in artifacts for flag in snapshot["flags"]): failures.append("confirmed flags must reference a stored artifact")
        elif assertion.startswith("event_chain:"):
            expected, position = assertion.split(":", 1)[1].split("->"), 0
            for event in snapshot.get("events") or []:
                if position < len(expected) and event.get("type") == expected[position]: position += 1
            if position != len(expected): failures.append(f"event chain missing: {' -> '.join(expected)}")
        elif assertion == "post_action_uses_observed_fields":
            forms = [item for item in snapshot.get("observed_forms") or [] if item.get("method") == "POST"]
            actions = [item for item in snapshot.get("actions") or [] if (item.get("arguments") or {}).get("method") == "POST"]
            fields, body = set((forms[0].get("fields") if forms else []) or []), ((actions[0].get("arguments") or {}).get("body") if actions else None)
            if not forms or not isinstance(body, dict) or not fields.issubset(body): failures.append("POST action must use the observed form field names")
        elif assertion == "failed_route_has_boundary":
            board = snapshot.get("board") or {}; hypotheses, boundaries = board.get("hypotheses") or [], [item for item in board.get("memory") or [] if item.get("kind") == "failure_boundary"]
            if not boundaries or not any(item.get("status") in {"inconclusive", "rejected"} for item in hypotheses): failures.append("failed route needs a hypothesis status and a failure boundary")
        elif assertion.startswith("semantic_retries_at_most:"):
            limit = int(assertion.rsplit(":", 1)[1]); counts = Counter(item.get("fingerprint") for item in snapshot.get("actions") or [] if item.get("fingerprint"))
            if any(count > limit for count in counts.values()): failures.append(f"semantic action retry budget exceeds {limit}")
        elif assertion == "local_action_has_workspace_artifact":
            artifacts = {item.get("id") for item in snapshot.get("artifacts") or []}; actions = [item for item in snapshot.get("actions") or [] if str(item.get("capability", "")).startswith("workspace.")]
            if not actions or not any(set(item.get("artifact_ids") or []) & artifacts for item in actions): failures.append("local action must preserve a workspace/tool artifact")
        else: failures.append(f"unknown assertion: {assertion}")
    return failures
