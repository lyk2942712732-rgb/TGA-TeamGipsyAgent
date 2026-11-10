"""Synthetic evidence producer; no real target or historical answer."""

import json
import os
from pathlib import Path

workspace = Path(os.environ["TGA3_WORKSPACE"])
agent = os.environ["TGA3_AGENT_ID"]
manifest = Path(os.environ["TGA3_LOGS"]) / f"{agent}-actions.json"
actions = json.loads(manifest.read_text())["actions"] if manifest.is_file() else []
observations = [a for a in actions if a["status"] == "completed" and "hosted_step.py" in a["action"].get("command", "")]
finding = Path(os.environ["TGA3_FINDINGS"]) / "synthetic.json"
if not observations:
    print("Synthetic observation: marker exists. HTB{hosted_synthetic_fixture}")
elif not finding.is_file():
    finding.write_text(
        json.dumps(
            {
                "action_id": observations[0]["action_id"],
                "claim": "Synthetic marker exists",
                "scope": "Isolated acceptance fixture; no real target",
            }
        )
    )
    print("Finding saved")
else:
    receipts = [json.loads(p.read_text()) for p in (workspace / ".tga3/publication-receipts").glob("*.json")]
    receipt = next((r for r in receipts if r.get("status") == "imported" and r["path"].startswith("/findings/")), None)
    submission = next(
        (a for a in actions if a["status"] == "completed" and "/challenges/submit" in a["action"].get("command", "")),
        None,
    )
    if receipt and submission:
        (workspace / "final-candidate.json").write_text(
            json.dumps(
                {
                    "conclusion": "HTB{hosted_synthetic_fixture}",
                    "rationale": "Verified synthetic marker and actual synthetic platform success receipt",
                    "finding_ids": [receipt["entry_id"]],
                    "submission_action_id": submission["action_id"],
                }
            )
        )
        print("Final candidate saved")
    else:
        print("Waiting for import/submission")
