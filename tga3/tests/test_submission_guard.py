import asyncio
import json

import httpx
import pytest
from test_submission_receipts import FLAG, URL, action, fixture, publish_file

from tga3.errors import ContractError
from tga3.submission_guard import SubmissionGuard
from tga3.submissions import Submissions

WRONG = "HTB{verified-outpt}"


def failed(cfg, q, **kw):
    command = f'curl -X POST {URL} -d \'{{"unique_code":"q-1","flag":"{WRONG}"}}\''
    return action(cfg, q, reply=kw.pop("reply", {"correct": False}), command=kw.pop("command", command), **kw)


async def guard_fixture(tmp_path, *, response=None, auto_submit=True):
    cfg, storage, c, parent, clock, q, body = await fixture(tmp_path, auto_submit=auto_submit)
    evidence = action(cfg, q, reply={"output": FLAG}, command="python3 solve.py")
    calls = []

    def request(req):
        calls.append(req)
        assert req.headers["BENCHMARK_TOKEN"] == "fixture-token"
        assert json.loads(req.content) == {"unique_code": "q-1", "flag": FLAG}
        if response == "timeout":
            raise httpx.ReadTimeout("fixture", request=req)
        return httpx.Response(
            200,
            json=response
            if response is not None
            else {"correct": True, "correct_flag_count": 1, "total_flag_count": 1},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(request))
    guard = SubmissionGuard(cfg, storage, client=client)
    return cfg, storage, c, parent, q, body, evidence, calls, client, guard


async def test_wrong_copy_resubmits_exact_output_once_survives_restart_and_can_publish(tmp_path):
    cfg, storage, c, parent, q, body, evidence, calls, client, guard = await guard_fixture(tmp_path)
    bad = failed(cfg, q)
    async with client:
        await asyncio.gather(*[guard.check(q.id, "worker-openai", bad["action_id"]) for _ in range(2)])
        result = await SubmissionGuard(cfg, storage, client=client).check(q.id, "worker-openai", bad["action_id"])
        assert len(calls) == 1
        assert result["status"] == "accepted"
        assert result["flag"] == FLAG and result["source_action_id"] == evidence["action_id"]
        assert result["response"]["correct"] is True
        assert "fixture-token" not in json.dumps(result)
        _, receipt = await publish_file(
            cfg, storage, q, body.model_copy(update={"submission_action_id": result["action_id"]})
        )
        assert receipt["status"] == "imported"
        await c.aoe.tick(parent.id)
        assert (await storage.get_task(q.id)).metadata["flag"] == FLAG


async def test_each_new_failed_action_gets_one_request_and_protection_does_not_loop(tmp_path):
    cfg, _, _, _, q, _, _, calls, client, guard = await guard_fixture(tmp_path, response={"correct": False})
    async with client:
        for _ in range(2):
            bad = failed(cfg, q)
            assert (await guard.check(q.id, "worker-openai", bad["action_id"]))["status"] == "rejected"
            await guard.check(q.id, "worker-openai", bad["action_id"])
        assert len(calls) == 2


@pytest.mark.parametrize(
    "response",
    ["timeout", {"correct": True, "correct_flag_count": 1, "total_flag_count": 2}, {"error": {"correct": True}}],
)
async def test_protection_error_or_partial_success_never_accepts_or_retries(tmp_path, response):
    cfg, _, _, _, q, body, _, calls, client, guard = await guard_fixture(tmp_path, response=response)
    bad = failed(cfg, q)
    async with client:
        result = await guard.check(q.id, "worker-openai", bad["action_id"])
        assert result["status"] != "accepted"
        await guard.check(q.id, "worker-openai", bad["action_id"])
        assert len(calls) == 1
        with pytest.raises(ContractError):
            Submissions(cfg).validate(q, body)


@pytest.mark.parametrize(
    "case",
    [
        "manual",
        "http_error",
        "auth",
        "nested_error",
        "other_question",
        "other_url",
        "old_attempt",
        "no_flag",
        "ambiguous",
        "literal",
        "reference",
    ],
)
async def test_ineligible_response_or_evidence_does_not_submit(tmp_path, case):
    cfg, storage, _, _, q, _, evidence, calls, client, guard = await guard_fixture(
        tmp_path, auto_submit=case != "manual"
    )
    reply = {"correct": False}
    command = None
    if case == "http_error":
        reply = 'HTTP=500 {"correct":false}'
    if case == "auth":
        reply = {"code": 401, "correct": False}
    if case == "nested_error":
        reply = {"error": {"correct": False}}
    if case == "other_question":
        command = f"curl {URL} -d q-10"
    if case == "other_url":
        command = "curl https://other.invalid/submit -d q-1"
    if case in {"no_flag", "ambiguous", "literal", "reference"}:
        workspace = cfg.task_path(q.id, "workspaces", "worker-openai")
        (workspace / evidence["artifact"]["output_path"]).write_text("no answer")
        if case == "ambiguous":
            action(cfg, q, reply=FLAG + " HTB{another-answer}", command="python solve.py")
        if case == "literal":
            action(cfg, q, reply=FLAG, command=f"echo {FLAG}")
        if case == "reference":
            action(cfg, q, reply=FLAG, command="curl https://github.com/public/writeup")
    bad = failed(cfg, q, reply=reply, **({"command": command} if command else {}))
    if case == "old_attempt":
        attempts = [{**a, "started_at": a["started_at"] + 100} for a in q.metadata["attempts"]]
        await storage.update_task(q.id, metadata={"attempts": attempts})
    async with client:
        result = await guard.check(q.id, "worker-openai", bad["action_id"])
        assert not calls
        assert result is None or result["status"] in {"no_flag", "ambiguous_flag"}


async def test_protected_receipt_is_bound_to_exact_flag_and_contract(tmp_path):
    cfg, _, _, _, q, body, _, _, client, guard = await guard_fixture(tmp_path)
    bad = failed(cfg, q)
    async with client:
        result = await guard.check(q.id, "worker-openai", bad["action_id"])
    submissions = Submissions(cfg)
    assert submissions.validate(q, body) == result["action_id"]
    with pytest.raises(ContractError):
        submissions.validate(q, body.model_copy(update={"conclusion": WRONG}))
    with pytest.raises(ContractError):
        submissions.validate(q, body.model_copy(update={"submission_action_id": "forged"}))


async def test_http_checkpoint_delivers_receipt_without_marking_solved(tmp_path):
    from tga3.app import create_app
    from tga3.docker_runtime import FakeContainerRuntime

    cfg, storage, _, _, q, _, _, calls, client, guard = await guard_fixture(tmp_path)
    bad = failed(cfg, q)
    app = create_app(cfg, storage, FakeContainerRuntime())
    app.state.submission_guard.client = client
    async with client, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        result = await http.post(
            f"/internal/blackboard/{q.id}/worker-openai/checkpoint",
            json={"submission_failure_action": bad["action_id"]},
        )
        assert result.status_code == 200
        assert result.json()["submission_protection"]["status"] == "accepted"
        recovered = await http.post(f"/internal/blackboard/{q.id}/worker-openai/checkpoint", json={})
        assert (
            recovered.json()["submission_protection"]["action_id"]
            == result.json()["submission_protection"]["action_id"]
        )
        assert (await storage.get_task(q.id)).metadata["status"] != "solved"
        assert len(calls) == 1


async def test_durable_claim_without_result_never_replays_request(tmp_path):
    import hashlib

    from tga3.submission_guard import records_root

    cfg, _, _, _, q, _, _, calls, client, guard = await guard_fixture(tmp_path)
    bad = failed(cfg, q)
    root = records_root(cfg, q)
    root.mkdir(parents=True)
    token = hashlib.sha256(("worker-openai:" + bad["action_id"]).encode()).hexdigest()
    (root / (token + ".claim")).touch()
    async with client:
        assert (await guard.check(q.id, "worker-openai", bad["action_id"]))["status"] == "claimed"
        assert not calls


async def test_other_worker_and_expired_attempt_cannot_trigger_protection(tmp_path):
    cfg, storage, _, _, q, _, _, calls, client, guard = await guard_fixture(tmp_path)
    bad = failed(cfg, q)
    async with client:
        assert await guard.check(q.id, "worker-claude", bad["action_id"]) is None
        await storage.update_task(q.id, metadata={"attempts": [{**a, "deadline": 0} for a in q.metadata["attempts"]]})
        assert await guard.check(q.id, "worker-openai", bad["action_id"]) is None
        assert not calls
