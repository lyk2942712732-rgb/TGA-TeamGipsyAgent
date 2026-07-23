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
            "task": {"name": "demo", "mode": "penetration_test", "target": "x", "scope": ["x"], "intensity": "normal"},
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
            "agent_events": [{"seq": 1, "type": "deadend", "payload": {"reason": "no login form found"}}],
        }
    )

    confirmed_section = report.split("## Confirmed Findings", 1)[1].split("## Unverified Leads", 1)[0]
    assert "Candidate only" not in confirmed_section
    assert "Candidate only [candidate]" in report
    assert "nuclei" in report
    assert "no login form found" in report
    assert "## CTF Flags" not in report
    assert "渗透测试 (penetration_test)" in report


def test_markdown_report_renders_plan_and_decision_trace():
    report = render_markdown_report(
        {
            "task": {"name": "demo", "mode": "penetration_test", "target": "x", "scope": ["x"], "intensity": "normal"},
            "artifacts": [],
            "findings": [],
            "flags": [],
            "agent_events": [
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


def test_markdown_report_renders_v2_session_outcome_and_seq_timeline():
    report = render_markdown_report(
        {
            "task": {"name": "runtime", "mode": "ctf", "target": "http://target", "scope": ["target"], "intensity": "normal"},
            "artifacts": [{"id": "artifact_1", "kind": "http_response", "tool": "http.request", "target": "http://target", "path": "a.json"}],
            "findings": [], "flags": [], "events": [],
            "session": {"status": "blocked", "turn_count": 3, "max_turns": 48, "stop_reason": "budget"},
            "solvers": [{"id": "solver_1", "role": "main", "status": "waiting"}],
            "board": {"hypotheses": [{"statement": "login has a testable route", "status": "inconclusive", "attack_class": "web", "entry_point": "/login", "evidence_artifact_ids": ["artifact_1"], "last_result": "Authorization: Bearer secret-value"}]},
            "actions": [{"id": "action_1", "status": "blocked", "capability": "http.request", "target": "http://target/login", "artifact_ids": ["artifact_1"], "summary": "scope boundary"}],
            "agent_events": [{"seq": 2, "type": "ACTION_FINISHED", "payload": {"summary": "scope boundary"}, "created_at": "2026-01-01T00:00:01Z"}, {"seq": 1, "type": "SESSION_STARTED", "payload": {}, "created_at": "2026-01-01T00:00:00Z"}],
        }
    )
    assert "## Session Outcome" in report
    assert "## Runtime Report (seq ordered)" in report
    assert "seq 1" in report and "seq 2" in report
    assert "[REDACTED]" in report

