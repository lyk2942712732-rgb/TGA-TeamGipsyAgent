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

