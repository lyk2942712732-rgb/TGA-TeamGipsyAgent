"""Run W1-W6 and emit CI- and frontend-loadable evaluation artifacts."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from assertions import evaluate_run
from runtime_cases import run_case
from tga.reporting.markdown_report import render_markdown_report


ROOT = Path(__file__).resolve().parent


def _public_replay(snapshot: dict[str, Any]) -> dict[str, Any]:
    events = sorted(snapshot.get("agent_events") or [], key=lambda item: int(item.get("seq") or 0))
    return {
        "task": snapshot.get("task") or {},
        "session": snapshot.get("session") or {},
        "solvers": snapshot.get("solvers") or [],
        "board": snapshot.get("board") or {"hypotheses": [], "memory": []},
        "actions": snapshot.get("actions") or [],
        "flags": snapshot.get("flags") or [],
        "findings": snapshot.get("findings") or [],
        "artifacts": snapshot.get("artifacts") or [],
        "events": events,
        "latest_seq": max((int(item.get("seq") or 0) for item in events), default=0),
        "schema_version": 2,
        "challenge_contract": snapshot.get("challenge_contract") or {},
    }


def run(output_dir: str | Path | None = None) -> dict[str, Any]:
    cases = json.loads((ROOT / "cases.yaml").read_text(encoding="utf-8"))
    owned_temp = tempfile.TemporaryDirectory(prefix="tga-evals-") if output_dir is None else None
    destination = Path(output_dir) if output_dir is not None else Path(owned_temp.name)  # type: ignore[union-attr]
    run_root = destination / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        for case in cases:
            case_id = case["case_id"]
            started = time.perf_counter()
            case_run = run_case(case_id, run_root)
            case_dir = destination / "cases" / case_id
            replay_path = case_dir / "replay.json"
            result = evaluate_run(
                case_run,
                round((time.perf_counter() - started) * 1000),
                replay_path=replay_path.relative_to(destination).as_posix() if output_dir is not None else None,
            )
            if output_dir is not None:
                case_dir.mkdir(parents=True, exist_ok=True)
                replay = _public_replay(case_run.snapshot)
                replay_path.write_text(json.dumps(replay, ensure_ascii=False, indent=2), encoding="utf-8")
                (case_dir / "eval-result.json").write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
                report_snapshot = dict(case_run.snapshot)
                report_snapshot["_artifact_base_path"] = str(run_root / replay["task"]["id"] / "artifacts")
                report_snapshot["evaluation"] = result.model_dump(mode="json")
                (case_dir / "report.md").write_text(render_markdown_report(report_snapshot), encoding="utf-8")
            results.append(result.model_dump(mode="json"))
        total = len(results)
        output = {
            "schema_version": 2,
            "passed": all(item["passed"] for item in results),
            "summary": {
                "cases": total,
                "solved": sum(item["outcome"] == "solved" for item in results),
                "success_rate": sum(item["passed"] for item in results) / total if total else 0,
                "confirmed_flags": sum(item["flag_confirmed"] for item in results),
                "average_actions": sum(item["action_count"] for item in results) / total if total else 0,
                "semantic_repeats": sum(item["semantic_repeat_count"] for item in results),
                "scope_rejections": sum(item["scope_rejection_count"] for item in results),
                "total_duration_ms": sum(item["duration_ms"] for item in results),
                "hint_utilization": sum(item["hint_utilization"] for item in results) / total if total else 0,
                "hint_to_flag_actions": [item["hint_to_flag_actions"] for item in results if item["hint_to_flag_actions"] is not None],
                "hint_to_flag_turns": [item["hint_to_flag_turns"] for item in results if item["hint_to_flag_turns"] is not None],
                "hint_to_flag_wall_ms": [item["hint_to_flag_wall_ms"] for item in results if item["hint_to_flag_wall_ms"] is not None],
                "duplicate_action_rate": sum(item["duplicate_action_rate"] for item in results) / total if total else 0,
                "consecutive_failures_without_new_hypothesis": sum(item["consecutive_failures_without_new_hypothesis"] for item in results),
                "latest_context_chars": [item["latest_context_chars"] for item in results],
                "artifact_retrieval_hits": sum(item["artifact_retrieval_hits"] for item in results),
                "observer_correction_adoption_rate": sum(item["observer_correction_adoption_rate"] for item in results) / total if total else 0,
                "observer_invalid_interruption_rate": sum(item["observer_invalid_interruption_rate"] for item in results) / total if total else 0,
                "flag_artifact_provenance_completeness": sum(item["flag_artifact_provenance_completeness"] for item in results) / total if total else 0,
                "unaudited_persistent_state_changes": sum(item["unaudited_persistent_state_changes"] for item in results),
            },
            "reliability": {
                "terminal_states": ["blocked", "failed", "cancelled", "completed"],
                "submission_oracle": "not_applicable_removed_by_v2_calibration",
                "replay_requires_database": False,
                "regression_matrix": [
                    {"condition": "llm_unconfigured", "boundary": "model", "expected": "fallback_solver_reaches_terminal_state", "covered_by": "tests/test_runtime_llm_solver.py"},
                    {"condition": "llm_json_invalid", "boundary": "model", "expected": "blocked_after_bounded_empty_plans", "covered_by": "tests/test_runtime_llm_solver.py"},
                    {"condition": "mcp_unavailable", "boundary": "bridge", "expected": "blocked_result", "covered_by": "tests/test_capabilities.py"},
                    {"condition": "http_timeout", "boundary": "executor", "expected": "bounded_failure", "covered_by": "tests/test_evals.py"},
                    {"condition": "challenge_submission_rejected", "boundary": "bridge", "expected": "not_applicable_removed_by_v2_calibration", "covered_by": "eval_contract"},
                    {"condition": "service_restart", "boundary": "manager", "expected": "durable_resume_to_terminal", "covered_by": "tests/test_runtime_manager.py"},
                    {"condition": "solver_crash", "boundary": "model", "expected": "failed", "covered_by": "tests/test_runtime_manager.py"},
                    {"condition": "sse_disconnect", "boundary": "ui_sse", "expected": "transport_closes_without_mutating_session", "covered_by": "tests/test_runtime_v2_routes.py"},
                    {"condition": "pause_resume_cancel", "boundary": "manager", "expected": "durable_control_state", "covered_by": "tests/test_runtime_manager.py"},
                ],
            },
            "cases": results,
        }
        if output_dir is not None:
            (destination / "eval-summary.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            (destination / "challenge-contracts.json").write_text(
                json.dumps([run_case_contract(case["case_id"]) for case in cases], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return output
    finally:
        if owned_temp is not None:
            owned_temp.cleanup()


def run_case_contract(case_id: str) -> dict[str, Any]:
    from challenges import build_fixture

    return build_fixture(case_id).contract.model_dump(mode="json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the isolated TGA W1-W6 evaluation suite.")
    parser.add_argument("--output-dir", default="runs/evals/latest", help="Directory for summary, reports, artifacts, and replay JSON.")
    args = parser.parse_args()
    output = run(args.output_dir)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
