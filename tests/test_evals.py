import sys
from pathlib import Path

def test_evaluation_fixtures_are_machine_verifiable():
    root = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(root / "evals"))
    try:
        from run_eval import run
        result = run()
    finally:
        sys.path.pop(0)
    assert result["passed"], result
    assert len(result["cases"]) == 4
    summary = result["summary"]
    assert summary["success_rate"] == 1.0
    assert summary["average_actions"] > 0
    assert summary["scope_rejections"] > 0
    assert summary["total_duration_ms"] >= 0
