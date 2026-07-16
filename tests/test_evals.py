import json
import sys
from pathlib import Path


EVAL_ROOT = Path(__file__).resolve().parents[1] / "evals"


def _eval_imports():
    if str(EVAL_ROOT) not in sys.path:
        sys.path.insert(0, str(EVAL_ROOT))
    from assertions import evaluate_run
    from run_eval import run
    from runtime_cases import run_case

    return evaluate_run, run, run_case


def test_evaluation_fixtures_are_machine_verifiable_and_replayable(tmp_path):
    _, run, _ = _eval_imports()
    output_dir = tmp_path / "evaluation"

    result = run(output_dir)

    assert result["passed"], result
    assert len(result["cases"]) == 6
    assert {item["case_id"] for item in result["cases"]} == {"W1", "W2", "W3", "W4", "W5", "W6"}
    assert result["summary"]["success_rate"] == 1.0
    assert result["summary"]["confirmed_flags"] == 6
    assert result["summary"]["scope_rejections"] == 0
    assert result["summary"]["semantic_repeats"] == 0
    for item in result["cases"]:
        assert item["outcome"] == "solved"
        assert item["artifact_provenance_ok"] is True
        assert item["coverage_gaps"] == []
        replay = json.loads((output_dir / item["replay_path"]).read_text(encoding="utf-8"))
        assert replay["schema_version"] == 2
        assert replay["latest_seq"] == replay["events"][-1]["seq"]
        assert "oracle" not in replay["challenge_contract"]
        report = (output_dir / "cases" / item["case_id"] / "report.md").read_text(encoding="utf-8")
        assert "## Challenge Contract" in report
        assert "## Confirmed Flag Provenance" in report
        assert "## Evaluation Closure" in report
    assert json.loads((output_dir / "eval-summary.json").read_text(encoding="utf-8"))["passed"] is True
    assert not list((output_dir / "cases").rglob("*.db"))


def test_tampered_artifact_cannot_be_scored_as_solved(tmp_path):
    evaluate_run, _, run_case = _eval_imports()
    case_run = run_case("W6", tmp_path / "runs")
    flag = case_run.snapshot["flags"][0]
    artifact = next(item for item in case_run.snapshot["artifacts"] if item["id"] == flag["evidence_artifact_id"])
    path = case_run.run_root / case_run.snapshot["task"]["id"] / "artifacts" / artifact["path"]
    path.write_text("model says flag{w6_faked000000000000}", encoding="utf-8")

    result = evaluate_run(case_run, 1)

    assert result.outcome == "failed"
    assert result.artifact_provenance_ok is False
    assert result.passed is False
    assert "artifact_provenance" in result.coverage_gaps


def test_public_challenge_contracts_never_contain_private_answers():
    _eval_imports()
    from challenges import build_fixture

    for case_id in ("W1", "W2", "W3", "W4", "W5", "W6"):
        fixture = build_fixture(case_id)
        public = json.dumps(fixture.contract.model_dump(mode="json"), ensure_ascii=False)
        assert fixture.oracle.expected_flag not in public
        assert "expected_flag" not in public


def test_failure_classifier_attributes_component_boundaries():
    _eval_imports()
    from assertions import classify_failure

    samples = {
        "model": {"session": {"status": "failed", "stop_reason": "solver_planning_failed"}, "agent_events": [{"type": "SOLVER_FAILED"}]},
        "executor": {"session": {"status": "failed", "stop_reason": "executor_failed"}},
        "http_timeout": {"session": {"status": "blocked"}, "actions": [{"result": {"error": {"code": "ACTION_TIMEOUT"}}}]},
        "bridge": {"session": {"status": "blocked"}, "actions": [{"result": {"error": {"code": "TOOL_RUNNER_UNAVAILABLE"}}}]},
        "scope": {"session": {"status": "blocked"}, "actions": [{"result": {"error": {"code": "OUT_OF_SCOPE"}}}]},
        "ui_sse": {"session": {"status": "failed", "stop_reason": "sse_transport_failed"}},
    }
    expected = {name: name for name in samples}
    expected["http_timeout"] = "executor"
    assert {name: classify_failure(snapshot) for name, snapshot in samples.items()} == expected
