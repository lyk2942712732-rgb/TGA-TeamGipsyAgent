"""Execute local v2 runtime cases and emit CI-friendly aggregate metrics."""
from __future__ import annotations

import json
import tempfile
import time
from collections import Counter
from pathlib import Path

from runtime_cases import run_case

ROOT = Path(__file__).resolve().parent


def _result(case_id: str, snapshot: dict, expected: dict, duration_ms: int) -> dict:
    actions = snapshot.get("actions") or []; events = snapshot.get("agent_events") or []
    flags = [item.get("value") for item in snapshot.get("flags") or []]
    artifacts = {item.get("id") for item in snapshot.get("artifacts") or []}
    fingerprints = Counter(json.dumps([item.get("capability"), item.get("target"), item.get("arguments")], sort_keys=True) for item in actions)
    scope_rejections = sum(1 for item in actions if ((item.get("error") or item.get("result", {}).get("error") or {}).get("code") in {"ACTION_NOT_ALLOWED", "OUT_OF_SCOPE", "REDIRECT_OUT_OF_SCOPE"}))
    checks = {
        "session_status": snapshot.get("session", {}).get("status") == expected["status"],
        "flag_provenance": (expected["flag"] in flags) if expected["flag"] else not flags,
        "artifacts_persisted": bool(artifacts) and all(set(item.get("artifact_ids") or []) <= artifacts for item in actions),
        "event_chain": all(name in [event.get("type") for event in events] for name in ("ACTION_PROPOSED", "ACTION_STARTED", "ACTION_FINISHED")),
        "post_is_post": not expected.get("post") or any((item.get("arguments") or {}).get("method") == "POST" for item in actions),
        "scope_rejection": not expected.get("scope_rejected") or scope_rejections > 0,
    }
    return {"id": case_id, "passed": all(checks.values()), "checks": checks, "confirmed_flags": len(flags), "actions": len(actions), "repeated_actions": sum(count - 1 for count in fingerprints.values() if count > 1), "empty_plans": sum(1 for event in events if event.get("type") == "PLAN_EMPTY"), "scope_rejections": scope_rejections, "duration_ms": duration_ms}


def run() -> dict:
    cases = json.loads((ROOT / "cases.yaml").read_text(encoding="utf-8")); results = []
    with tempfile.TemporaryDirectory(prefix="tga-evals-") as raw_root:
        root = Path(raw_root)
        for case in cases:
            started = time.perf_counter(); snapshot, expected = run_case(case["id"], root)
            results.append(_result(case["id"], snapshot, expected, round((time.perf_counter() - started) * 1000)))
    total = len(results)
    return {"passed": all(item["passed"] for item in results), "summary": {"cases": total, "success_rate": sum(item["passed"] for item in results) / total if total else 0, "confirmed_flags": sum(item["confirmed_flags"] for item in results), "average_actions": sum(item["actions"] for item in results) / total if total else 0, "repeated_actions": sum(item["repeated_actions"] for item in results), "empty_plans": sum(item["empty_plans"] for item in results), "scope_rejections": sum(item["scope_rejections"] for item in results), "total_duration_ms": sum(item["duration_ms"] for item in results)}, "cases": results}


if __name__ == "__main__":
    output = run(); print(json.dumps(output, ensure_ascii=False, indent=2)); raise SystemExit(0 if output["passed"] else 1)
