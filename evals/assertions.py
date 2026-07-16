"""Strict, artifact-backed assertions for challenge evaluation runs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from contracts import EvalResult


_SCOPE_ERRORS = {"ACTION_NOT_ALLOWED", "OUT_OF_SCOPE", "REDIRECT_OUT_OF_SCOPE"}


def evaluate_run(case_run: Any, duration_ms: int, *, replay_path: str | None = None) -> EvalResult:
    fixture = case_run.fixture
    contract = fixture.contract
    oracle = fixture.oracle
    snapshot = case_run.snapshot
    actions = snapshot.get("actions") or []
    events = snapshot.get("agent_events") or []
    event_types = [str(event.get("type")) for event in events]
    artifacts = {item.get("id"): item for item in snapshot.get("artifacts") or []}
    flags = snapshot.get("flags") or []
    task = snapshot.get("task") or {}
    task_id = str(task.get("id") or "")

    provenance_ok = _artifact_provenance_ok(
        flags=flags,
        artifacts=artifacts,
        expected_flag=oracle.expected_flag,
        artifact_root=Path(case_run.run_root) / task_id / "artifacts",
        task_id=task_id,
    )
    flag_confirmed = (
        len(flags) == 1
        and flags[0].get("value") == oracle.expected_flag
        and any(
            event.get("type") == "FLAG_CONFIRMED"
            and (event.get("payload") or {}).get("value") == oracle.expected_flag
            and (event.get("payload") or {}).get("evidence_artifact_id") == flags[0].get("evidence_artifact_id")
            for event in events
        )
    )
    fingerprints = Counter(
        json.dumps(
            [item.get("capability"), item.get("target"), item.get("arguments")],
            sort_keys=True,
            ensure_ascii=False,
        )
        for item in actions
    )
    semantic_repeat_count = sum(max(0, count - 1) for count in fingerprints.values())
    max_attempts = max(fingerprints.values(), default=0)
    scope_rejections = sum(
        1
        for item in actions
        if (((item.get("result") or {}).get("error") or {}).get("code") in _SCOPE_ERRORS)
    )
    checks = {
        "session_completed_by_flag_gate": (snapshot.get("session") or {}).get("status") == "completed"
        and (snapshot.get("session") or {}).get("stop_reason") == "confirmed_flag",
        "flag_gate_confirmed_private_oracle": flag_confirmed,
        "artifact_provenance": provenance_ok,
        "required_event_chain": _ordered_event_chain(event_types, contract.required_events),
        "submission_endpoint_absent": not any(name.startswith("SUBMISSION_") for name in event_types),
        "action_budget": len(actions) <= contract.max_actions,
        "manager_semantic_budget": max_attempts <= contract.manager_semantic_attempt_budget,
        "executor_semantic_budget": max_attempts <= contract.executor_semantic_attempt_budget,
        "zero_scope_rejections": scope_rejections == 0,
        "required_capabilities": set(contract.required_capabilities) <= {str(item.get("capability")) for item in actions},
        "private_oracle_not_in_contract": oracle.expected_flag not in json.dumps(contract.model_dump(mode="json"), ensure_ascii=False),
        **_case_checks(case_run),
    }
    passed = all(checks.values())
    status = (snapshot.get("session") or {}).get("status")
    outcome = "solved" if passed else "blocked" if status == "blocked" else "failed"
    return EvalResult(
        case_id=contract.case_id,
        outcome=outcome,
        flag_confirmed=flag_confirmed,
        artifact_provenance_ok=provenance_ok,
        action_count=len(actions),
        semantic_repeat_count=semantic_repeat_count,
        scope_rejection_count=scope_rejections,
        solver_roles=contract.solver_roles,
        coverage_gaps=[name for name, ok in checks.items() if not ok],
        failure_domain=classify_failure(snapshot, checks),
        checks=checks,
        duration_ms=duration_ms,
        replay_path=replay_path,
        passed=passed,
    )


def classify_failure(snapshot: dict[str, Any], checks: dict[str, bool] | None = None) -> str:
    if checks and all(checks.values()):
        return "none"
    session = snapshot.get("session") or {}
    reason = str(session.get("stop_reason") or "").lower()
    actions = snapshot.get("actions") or []
    error_codes = {
        str((((action.get("result") or {}).get("error") or {}).get("code") or "")).upper()
        for action in actions
    }
    events = snapshot.get("agent_events") or snapshot.get("events") or []
    event_types = {str(item.get("type") or "") for item in events}
    if "scope" in reason or error_codes & _SCOPE_ERRORS:
        return "scope"
    if "solver" in reason or "model" in reason or "SOLVER_FAILED" in event_types:
        return "model"
    if "executor" in reason or "EXECUTOR_FAILED" in error_codes:
        return "executor"
    if error_codes & {"HTTP_REQUEST_FAILED", "HTTP_EXECUTION_FAILED", "ACTION_TIMEOUT"}:
        return "executor"
    if error_codes & {"TOOL_RUNNER_UNAVAILABLE", "TOOL_NOT_AVAILABLE", "TOOL_EXECUTION_FAILED", "TOOL_TIMEOUT"}:
        return "bridge"
    if "sse" in reason or "ui" in reason:
        return "ui_sse"
    if reason.startswith("fixture"):
        return "fixture"
    if reason or session.get("status") in {"blocked", "failed", "cancelled"}:
        return "manager"
    return "unknown"


def _artifact_provenance_ok(
    *, flags: list[dict[str, Any]], artifacts: dict[str, dict[str, Any]], expected_flag: str, artifact_root: Path, task_id: str
) -> bool:
    if len(flags) != 1 or flags[0].get("value") != expected_flag:
        return False
    artifact = artifacts.get(flags[0].get("evidence_artifact_id"))
    if not artifact or artifact.get("task_id") != task_id or not artifact.get("path"):
        return False
    root = artifact_root.resolve()
    path = (root / str(artifact["path"])).resolve()
    try:
        path.relative_to(root)
        raw = path.read_bytes()
    except (OSError, ValueError):
        return False
    return hashlib.sha256(raw).hexdigest() == artifact.get("sha256") and expected_flag.encode("utf-8") in raw


def _ordered_event_chain(actual: list[str], expected: list[str]) -> bool:
    position = 0
    for event_type in actual:
        if position < len(expected) and event_type == expected[position]:
            position += 1
    return position == len(expected)


def _case_checks(case_run: Any) -> dict[str, bool]:
    fixture = case_run.fixture
    case_id = fixture.contract.case_id
    actions = case_run.snapshot.get("actions") or []
    requests = fixture.requests
    observed_paths = tuple(item.path for item in requests)
    checks: dict[str, bool] = {
        "fixture_request_path": all(path in observed_paths for path in fixture.oracle.expected_paths),
    }
    if case_id == "W2":
        post_actions = [item for item in actions if (item.get("arguments") or {}).get("method") == "POST"]
        body = (post_actions[0].get("arguments") or {}).get("body") if post_actions else None
        query = (post_actions[0].get("arguments") or {}).get("query") if post_actions else None
        server_posts = [item for item in requests if item.method == "POST" and item.path == "/session"]
        checks["post_uses_observed_fields"] = isinstance(body, dict) and fixture.oracle.expected_post_fields <= set(body)
        checks["post_payload_not_in_query"] = not query and bool(server_posts) and not server_posts[0].query
    elif case_id == "W3":
        python_actions = [item for item in actions if item.get("capability") == "workspace.python"]
        signed_actions = [item for item in actions if (item.get("arguments") or {}).get("path") == "/signed-proof"]
        headers = (signed_actions[0].get("arguments") or {}).get("headers") if signed_actions else {}
        checks["dynamic_signature_script_artifact"] = bool(python_actions and (python_actions[0].get("result") or {}).get("artifact_ids"))
        checks["signed_header_request"] = bool((headers or {}).get("X-Challenge-Signature"))
    elif case_id == "W4":
        object_actions = [item for item in actions if str((item.get("arguments") or {}).get("path", "")).startswith("/api/records/")]
        artifact_sets = [tuple((item.get("result") or {}).get("artifact_ids") or []) for item in object_actions]
        checks["object_difference_evidence"] = len(object_actions) == 2 and len(set(artifact_sets)) == 2
    elif case_id == "W5":
        reads = [item for item in actions if item.get("capability") == "workspace.read"]
        checks["local_attachment_artifact"] = bool(reads and (reads[0].get("result") or {}).get("artifact_ids"))
    elif case_id == "W6":
        python_actions = [item for item in actions if item.get("capability") == "workspace.python"]
        source = str((python_actions[0].get("arguments") or {}).get("source") or "") if python_actions else ""
        checks["reproducible_decode_script"] = "bytes(encoded).decode" in source and bool((python_actions[0].get("result") or {}).get("artifact_ids"))
    return checks
