import json

import pytest

from tga3.flag_signals import declared_format, flag_pattern


@pytest.mark.parametrize("format,text,expected", [
    ("SEKAI{...}", "iteratorSEKAI{real} HTB{wrong}", ["SEKAI{real}"]),
    ("Flag 格式：`SEKAI{...}`", "SEKAI{real} sekai{wrong}", ["SEKAI{real}"]),
    ("flag{uuid}", "flag{not-uuid} flag{d45c1969-1cb0-47ca-87b3-cc3493b9d88a}",
     ["flag{d45c1969-1cb0-47ca-87b3-cc3493b9d88a}"]),
    ("flag{md5}", "flag{abc} flag{" + "a" * 32 + "}", ["flag{" + "a" * 32 + "}"]),
    ("", "HTB{legacy}", ["HTB{legacy}"]),
    ("unsupported specification", "HTB{unrelated}", []),
    ("TEAM.CTF{...}", "TEAMxCTF{wrong} TEAM.CTF{real}", ["TEAM.CTF{real}"]),
])
def test_declared_envelope(format, text, expected):
    assert flag_pattern(format).findall(text) == expected


def test_only_labelled_formats_are_read_from_descriptions():
    assert declared_format({"description": "Flag 格式：SEKAI{...}"}) == "SEKAI{...}"
    assert not declared_format({"description": "A historical answer is HTB{wrong}"})


async def test_guard_uses_declared_format_from_original_source(tmp_path):
    from test_submission_guard import failed, guard_fixture
    from test_submission_receipts import action

    cfg, _, _, _, q, _, evidence, calls, client, guard = await guard_fixture(tmp_path)
    # Replace prior output; only the original source declares this legacy task's format.
    workspace = cfg.task_path(q.id, "workspaces", "worker-openai")
    (workspace / evidence["artifact"]["output_path"]).write_text("no flag")
    (cfg.task_path(q.id) / "source.json").write_text(json.dumps({"flag_format": "SEKAI{...}"}))
    action(cfg, q, reply="iteratorSEKAI{exact_output} HTB{unrelated}", command="python solve.py")
    rejected = failed(cfg, q)
    async with client:
        # The fixture checks the submitted flag, so replace its transport with a specific check.
        import httpx

        def request(req):
            calls.append(req)
            assert json.loads(req.content)["flag"] == "SEKAI{exact_output}"
            return httpx.Response(200, json={"correct": True})

        guard.client = httpx.AsyncClient(transport=httpx.MockTransport(request))
        async with guard.client:
            result = await guard.check(q.id, "worker-openai", rejected["action_id"])
            await guard.check(q.id, "worker-openai", rejected["action_id"])
    assert result["flag"] == "SEKAI{exact_output}" and result["status"] == "accepted"
    assert len(calls) == 1
