from tga.workers.output_parser import parse_markers


def test_parse_markers():
    parsed = parse_markers(
        "\n".join([
            "VERIFIED_FACT=login page exists",
            "UNVERIFIED_LEAD=maybe sqli",
            "FOUND_FLAG=flag{abc123}",
            "TOOL_ERROR=nmap|timeout",
        ]),
        task_id="task_1",
    )
    assert parsed.facts == ["login page exists"]
    assert parsed.leads == ["maybe sqli"]
    assert parsed.flags == ["flag{abc123}"]
    assert parsed.errors == ["nmap|timeout"]

