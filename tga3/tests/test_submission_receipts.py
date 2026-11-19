import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from test_aoe_runtime import setup

from tga3.aoe_models import AOEQuestionInput, active_attempts
from tga3.domain import SYSTEM_ACTOR, Artifact, ArtifactRef, FinalCandidateBody, PublishRequest
from tga3.file_publications import FilePublications
from tga3.submissions import SubmissionRejected, Submissions, SubmissionSpec, classify_receipt
from tga3.target_instances import TargetRequest
from tga3.workers.action_journal import ActionJournal

URL = "https://benchmark.invalid/openapi/v1/challenges/submit"
FLAG = "HTB{verified-output}"


async def fixture(tmp_path, *, auto_submit=True, spec: SubmissionSpec | None = None, no_spec: bool = False):
    cfg, storage, runtime, coordinator, parent, clock = await setup(tmp_path)
    if not auto_submit:
        await storage.update_task(parent.id, metadata={"aoe_auto_submit_flag": False})
    clock[0] = time.time()
    effective_spec = (
        None
        if no_spec
        else (
            spec
            or SubmissionSpec(
                request=TargetRequest(
                    method="POST",
                    url=URL,
                    headers={"BENCHMARK_TOKEN": "fixture-token"},
                    body={"unique_code": "q-1", "flag": "{flag}"},
                )
            )
        )
    )
    await coordinator.aoe.register_questions(
        parent.id,
        [
            AOEQuestionInput(
                source_key="q-1",
                title="Question",
                description="Fixture",
                difficulty="easy",
                submission=effective_spec,
            )
        ],
    )
    await coordinator.aoe.finish_collection(parent.id, 1)
    await coordinator.aoe.tick(parent.id)
    q = (await storage.list_questions(parent.id))[0]
    await coordinator.aoe.action_started(q.id, "worker-openai", active_attempts(q)[0]["id"])
    proof = await storage.register_artifact(
        Artifact(
            task_id=q.id,
            created_by=SYSTEM_ACTOR,
            name="proof",
            storage_path="fixture",
            media_type="text/plain",
            sha256="0" * 64,
            size_bytes=0,
        )
    )
    finding = await storage.publish(
        q.id,
        SYSTEM_ACTOR,
        PublishRequest(
            kind="finding",
            body={"claim": "Verified flag from assigned target"},
            idempotency_key="proof",
            artifact_refs=[ArtifactRef(artifact_id=proof.id, locator="stdout")],
        ),
    )
    q = await storage.get_task(q.id)
    await storage.update_agent(q.id, "worker-openai", metadata={"blackboard_cursor": q.blackboard_seq})
    body = FinalCandidateBody(conclusion=FLAG, rationale="Actual output", finding_ids=[finding.id])
    return cfg, storage, coordinator, parent, clock, q, body


def action(cfg, q, *, reply, command=None, agent="worker-openai", tool="Bash"):
    workspace = cfg.task_path(q.id, "workspaces", agent)
    workspace.mkdir(parents=True, exist_ok=True)
    journal = ActionJournal(
        cfg.task_path(q.id, "logs", agent),
        identity={"task_id": str(q.id), "agent_id": agent},
        secret="",
        output_directory=workspace,
    )
    return journal.record(
        str(uuid4()),
        tool,
        inputs={
            "command": command
            or f"curl -sS -X POST {URL} -d '{{\"unique_code\":\"q-1\",\"flag\":\"{FLAG}\"}}'"
        },
        result={
            "type": "tool_result",
            "content": {
                "stdout": reply if isinstance(reply, str) else json.dumps(reply),
                "stderr": "",
            },
            "exit_code": 0,
        },
        status="completed",
    )


async def publish_file(cfg, storage, q, body, *, client: httpx.AsyncClient | None = None):
    workspace = cfg.task_path(q.id, "workspaces", "worker-openai")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "final-candidate.json").write_text(body.model_dump_json())
    from tga3.blackboard import Blackboard

    submissions = Submissions(cfg, client=client)
    importer = FilePublications(cfg, Blackboard(storage), submissions=submissions)
    return importer, (await importer.sync_final(q.id, "worker-openai"))[0]


def mock_client(status: int = 200, body=None, *, calls: list | None = None) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req)
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body if body is not None else {"correct": True})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_system_managed_submission_submits_from_host_without_worker_curl_and_hides_secrets(tmp_path):
    cfg, storage, c, parent, _, q, body = await fixture(tmp_path)
    # Worker context must NOT leak credentials and MUST advertise managed_by_system + blocked_url.
    ctx_path = cfg.task_path(q.id, "workspaces", "worker-openai") / ".tga3/submission.json"
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert ctx["managed_by_system"] is True
    assert ctx["blocked_url"] == URL
    assert "fixture-token" not in ctx_path.read_text(encoding="utf-8")

    calls: list[httpx.Request] = []
    client = mock_client(200, {"code": 200, "msg": "OK", "data": {"result": True}}, calls=calls)
    async with client:
        importer, receipt = await publish_file(cfg, storage, q, body, client=client)
        assert receipt["status"] == "imported"
        assert len(calls) == 1
        assert calls[0].headers["BENCHMARK_TOKEN"] == "fixture-token"
        assert json.loads(calls[0].content) == {"unique_code": "q-1", "flag": FLAG}
        canonical = json.loads((cfg.task_path(q.id) / "final-candidate.json").read_text())
        assert canonical["submission_action_id"].startswith("system-submission-")
        assert canonical["conclusion"] == FLAG
        # Idempotent: second sync does not re-submit.
        assert await importer.sync_final(q.id, "worker-openai") == []
        assert len(calls) == 1
    await c.aoe.tick(parent.id)
    assert (await storage.get_task(q.id)).metadata["status"] == "solved"
    assert (await storage.get_task(q.id)).metadata["flag"] == FLAG


async def test_duplicate_402_chinese_already_solved_receipt_is_accepted(tmp_path):
    cfg, storage, c, parent, _, q, body = await fixture(tmp_path)
    client = mock_client(402, {"code": 402, "msg": "该团队已解出该题目", "data": None})
    async with client:
        _, receipt = await publish_file(cfg, storage, q, body, client=client)
        assert receipt["status"] == "imported"
    await c.aoe.tick(parent.id)
    assert (await storage.get_task(q.id)).metadata["status"] == "solved"


async def test_manual_submission_and_missing_spec_publish_flag_without_network(tmp_path):
    cfg, storage, coordinator, parent, _, q, body = await fixture(tmp_path, auto_submit=False)
    assert q.metadata["aoe_auto_submit_flag"] is False
    worker_launch = next(spec for spec in coordinator.containers.launched if spec.task_id == q.id)
    assert worker_launch.aoe_auto_submit_flag is False
    workspace = cfg.task_path(q.id, "workspaces", "worker-openai")
    assert not (workspace / ".tga3/submission.json").exists()

    _, receipt = await publish_file(cfg, storage, q, body)
    assert receipt["status"] == "imported"
    canonical = json.loads((cfg.task_path(q.id) / "final-candidate.json").read_text())
    assert canonical["conclusion"] == FLAG
    assert canonical["submission_action_id"] is None
    await coordinator.aoe.tick(parent.id)
    assert (await storage.get_task(q.id)).metadata["flag"] == FLAG

    # When auto-submit is enabled but platform has no static submission contract, do not deadlock.
    cfg2, storage2, coord2, parent2, _, q2, body2 = await fixture(tmp_path / "no-spec", no_spec=True)
    _, receipt2 = await publish_file(cfg2, storage2, q2, body2)
    assert receipt2["status"] == "imported"
    await coord2.aoe.tick(parent2.id)
    assert (await storage2.get_task(q2.id)).metadata["status"] == "solved"


@pytest.mark.parametrize(
    "status,reply",
    [
        (200, {"correct": False, "awarded": 0}),
        (200, {"code": 200, "data": {"result": False}, "msg": "flag错误"}),
        (200, {"correct": True, "correct_flag_count": 1, "total_flag_count": 2}),
        (500, {"correct": True}),
        (200, {"error": {"correct": True}}),
    ],
)
async def test_platform_rejection_partial_or_error_blocks_publication(tmp_path, status, reply):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    client = mock_client(status, reply)
    async with client:
        _, receipt = await publish_file(cfg, storage, q, body, client=client)
        assert receipt["status"] == "submission_rejected"
    assert not any(e.kind == "final_candidate" for e in await storage.list_entries(q.id))
    assert not (cfg.task_path(q.id) / "final-candidate.json").exists()


async def test_classify_receipt_covers_custom_and_fallback_formats():
    spec = SubmissionSpec(request=TargetRequest(method="POST", url=URL, body={"id": 11, "flag": "{flag}"}))
    assert classify_receipt(spec, {"code": 200, "msg": "OK", "data": {"result": True}}, 200) == "accepted"
    assert classify_receipt(spec, {"code": 402, "msg": "该团队已解出该题目"}, 402) == "duplicate"
    assert classify_receipt(spec, {"code": "duplicate", "message": "already submitted"}, 200) == "duplicate"
    partial = {"correct": True, "correct_flag_count": 1, "total_flag_count": 2}
    assert classify_receipt(spec, partial, 200) == "partial_incomplete"
    assert classify_receipt(spec, {"code": 200, "data": {"result": False}}, 200) == "rejected"
    assert classify_receipt(spec, {"error": {"correct": True}}, 200) == "error"


async def test_partial_flags_are_not_a_completed_question(tmp_path):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    client = mock_client(200, {"correct": True, "correct_flag_count": 1, "total_flag_count": 2})
    async with client:
        with pytest.raises(SubmissionRejected, match="not complete"):
            await Submissions(cfg, client=client).submit(q, body)


async def test_closing_window_extends_same_attempt_until_final_publication(tmp_path):
    cfg, storage, c, parent, clock, q, body = await fixture(tmp_path)
    original = active_attempts(q)[0]
    saved = action(cfg, q, reply={"output": FLAG}, command="python3 solve.py")
    clock[0] = original["deadline"] - 31
    await c.aoe.tick(parent.id)
    assert active_attempts(await storage.get_task(q.id))[0]["deadline"] == original["deadline"]
    clock[0] += 2
    await c.aoe.tick(parent.id)
    held = active_attempts(await storage.get_task(q.id))[0]
    assert held["id"] == original["id"] and held["deadline"] > original["deadline"]
    assert held["completion_hold_action"] == saved["action_id"]
    clock[0] = held["deadline"] - 1
    await c.aoe.tick(parent.id)
    assert active_attempts(await storage.get_task(q.id))[0]["deadline"] > held["deadline"]
    client = mock_client(200, {"correct": True, "correct_flag_count": 1, "total_flag_count": 1})
    async with client:
        _, receipt = await publish_file(cfg, storage, q, body, client=client)
        assert receipt["status"] == "imported"
    await c.aoe.tick(parent.id)
    assert (await storage.get_task(q.id)).metadata["status"] == "solved"
    assert not active_attempts(await storage.get_task(q.id))


@pytest.mark.parametrize(
    "tool,command",
    [
        ("Read", "read output.txt"),
        ("Write", "save file"),
        ("Bash", f"echo '{FLAG}'"),
        ("Bash", "curl https://github.com/public/writeup"),
    ],
)
async def test_reference_or_literal_flags_do_not_extend_budget(tmp_path, tool, command):
    cfg, storage, c, parent, clock, q, _ = await fixture(tmp_path)
    original = active_attempts(q)[0]
    action(cfg, q, reply={"output": FLAG}, command=command, tool=tool)
    clock[0] = original["deadline"] - 10
    await c.aoe.tick(parent.id)
    assert active_attempts(await storage.get_task(q.id))[0]["deadline"] == original["deadline"]


async def test_old_attempt_output_cannot_extend_current_attempt(tmp_path):
    cfg, storage, c, parent, clock, q, _ = await fixture(tmp_path)
    action(cfg, q, reply={"output": FLAG}, command="python3 solve.py")
    log = cfg.task_path(q.id, "logs", "worker-openai") / "worker-openai-actions.json"
    document = json.loads(log.read_text())
    document["actions"][0]["started_at"] = datetime.fromtimestamp(clock[0] - 100, UTC).isoformat()
    log.write_text(json.dumps(document))
    original = active_attempts(q)[0]
    clock[0] = original["deadline"] - 10
    await c.aoe.tick(parent.id)
    assert active_attempts(await storage.get_task(q.id))[0]["deadline"] == original["deadline"]


async def test_grace_scan_is_cached_and_survives_runtime_recreation(tmp_path, monkeypatch):
    cfg, storage, c, parent, clock, q, _ = await fixture(tmp_path)
    original = active_attempts(q)[0]
    clock[0] = original["deadline"] - 20
    calls = []
    read = c.aoe.submissions._actions
    monkeypatch.setattr(c.aoe.submissions, "_actions", lambda *args: (calls.append(args), read(*args))[1])
    await c.aoe.tick(parent.id)
    await c.aoe.tick(parent.id)
    assert not calls  # No journal exists, no full-log reads.
    action(cfg, q, reply={"output": FLAG}, command="python3 solve.py")
    await c.aoe.tick(parent.id)
    assert len(calls) == 1
    held = active_attempts(await storage.get_task(q.id))[0]
    from tga3.coordinator import TaskCoordinator
    from tga3.docker_runtime import FakeContainerRuntime

    restored = TaskCoordinator(cfg, storage, FakeContainerRuntime())
    restored.aoe.clock = lambda: held["deadline"] - 10
    await restored.aoe.restore_paths()
    await restored.aoe._recover_attempts(parent.id)
    assert active_attempts(await storage.get_task(q.id))[0]["deadline"] > held["deadline"]


async def test_aoe_cli_uses_the_runtime_deadline_not_a_fixed_cycle_timer(tmp_path, monkeypatch):
    from tga3.workers.cli_adapter import CodexAdapter

    for key, value in {
        "TGA3_WORKSPACE": str(tmp_path),
        "TGA3_LOGS": str(tmp_path / "logs"),
        "TGA3_TASK_ID": str(uuid4()),
        "TGA3_AGENT_ID": "worker-openai",
        "TGA3_AGENT_ROLE": "worker",
        "TGA3_AGENT_RUNTIME": "codex_cli",
        "TGA3_AOE_PARENT_ID": str(uuid4()),
        "TGA3_API_KEY": "fixture",
        "TGA3_MODEL_NAME": "fixture",
        "TGA3_PROVIDER_PROTOCOL": "openai_responses",
        "TGA3_CLI_TIMEOUT_SECONDS": "1800",
    }.items():
        monkeypatch.setenv(key, value)
    assert CodexAdapter().timeout is None


@pytest.mark.parametrize("same_origin", [True, False])
async def test_legacy_contract_uses_only_a_documented_url_and_same_origin_credentials(tmp_path, same_origin):
    cfg, storage, _, _, _, q, _ = await fixture(tmp_path)
    q = await storage.update_task(
        q.id, metadata={"submission_instructions": f"POST {URL} with JSON unique_code and flag"}
    )
    target = {
        "start": {
            "url": URL if same_origin else "https://other.invalid/start",
            "headers": {"BENCHMARK_TOKEN": "legacy-fixture"},
        }
    }
    spec = Submissions.legacy_spec(q, target)
    if same_origin:
        assert spec.request.url == URL and spec.request.headers["BENCHMARK_TOKEN"] == "legacy-fixture"
        assert spec.request.body == {"unique_code": "q-1", "flag": "{flag}"}
    else:
        assert spec is None
