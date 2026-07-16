from tga.core.flag_gate import flag_ok


def test_flag_requires_real_output():
    assert flag_ok(
        "flag{real_123}",
        flag_format=r"flag\{[^}]+\}",
        raw_output="server printed flag{real_123}",
    )


def test_placeholder_rejected():
    assert not flag_ok("flag{...}", flag_format=r"flag\{[^}]+\}", raw_output="flag{...}")


def test_claim_without_output_rejected():
    assert not flag_ok("flag{real_123}", flag_format=r"flag\{[^}]+\}", raw_output="")


def test_default_flag_format_accepts_common_ctf_prefix_when_evidenced():
    assert flag_ok(
        "NSSCTF{real_123}",
        flag_format=r"flag\{[^}]+\}",
        raw_output="server printed NSSCTF{real_123}",
    )
