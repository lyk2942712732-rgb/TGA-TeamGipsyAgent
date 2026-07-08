from tga.reporting.markdown_report import render_markdown_report


def test_markdown_report_renders_sections():
    report = render_markdown_report(
        {
            "task": {"name": "demo", "mode": "ctf", "target": "x", "scope": ["x"], "intensity": "normal"},
            "artifacts": [],
            "findings": [],
            "flags": [],
            "events": [],
        }
    )
    assert "# TGA Report" in report
    assert "## Confirmed Findings" in report
    assert "## Decision Trace" in report
    assert "## Dead Ends" in report


def test_markdown_report_keeps_candidates_out_of_confirmed():
    report = render_markdown_report(
        {
            "task": {"name": "demo", "mode": "web_audit", "target": "x", "scope": ["x"], "intensity": "normal"},
            "artifacts": [{"id": "a1", "kind": "stdout", "tool": "nuclei", "target": "x", "path": "a1.txt"}],
            "findings": [
                {
                    "id": "f1",
                    "title": "Candidate only",
                    "target": "x",
                    "severity": "medium",
                    "status": "candidate",
                }
            ],
            "flags": [],
            "events": [{"type": "deadend", "payload": {"reason": "no login form found"}}],
        }
    )

    confirmed_section = report.split("## Confirmed Findings", 1)[1].split("## CTF Flags", 1)[0]
    assert "Candidate only" not in confirmed_section
    assert "Candidate only [candidate]" in report
    assert "nuclei" in report
    assert "no login form found" in report


def test_markdown_report_renders_plan_and_decision_trace():
    report = render_markdown_report(
        {
            "task": {"name": "demo", "mode": "web_audit", "target": "x", "scope": ["x"], "intensity": "normal"},
            "artifacts": [],
            "findings": [],
            "flags": [],
            "events": [
                {
                    "type": "PLAN_CREATED",
                    "payload": {
                        "rationale": "Recon in scope, verify likely issues, and report.",
                        "plan": {
                            "steps": [
                                {
                                    "order": 1,
                                    "kind": "recon",
                                    "risk": "passive",
                                    "required_tools": ["whatweb"],
                                    "rationale": "Map the reachable surface.",
                                }
                            ]
                        },
                    },
                },
                {
                    "type": "DECISION_TRACE",
                    "intent_id": "intent_recon",
                    "payload": {
                        "summary": "Run recon intent",
                        "rationale": "Map the reachable surface before verification.",
                    },
                },
            ],
        }
    )

    assert "Recon in scope" in report
    assert "DECISION_TRACE [intent_recon]" in report
    assert "Map the reachable surface" in report

