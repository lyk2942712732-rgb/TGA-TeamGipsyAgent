import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_aoe_runtime import setup

from tga3.aoe_models import AOEQuestionInput, active_attempts
from tga3.domain import SYSTEM_ACTOR, Artifact, ArtifactRef, FinalCandidateBody, PublishRequest
from tga3.file_publications import FilePublications
from tga3.submissions import SubmissionRejected, SubmissionSpec
from tga3.target_instances import TargetRequest
from tga3.workers.action_journal import ActionJournal

URL = "https://benchmark.invalid/openapi/v1/challenges/submit"
FLAG = "HTB{verified-output}"


async def fixture(tmp_path):
    cfg, storage, runtime, coordinator, parent, clock = await setup(tmp_path)
    clock[0] = time.time()
    spec = SubmissionSpec(request=TargetRequest(method="POST", url=URL,
        headers={"BENCHMARK_TOKEN": "fixture-token"}, body={"unique_code": "q-1", "flag": "{flag}"}))
    await coordinator.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key="q-1", title="Question", description="Fixture", difficulty="easy", submission=spec)])
    await coordinator.aoe.finish_collection(parent.id, 1)
    await coordinator.aoe.tick(parent.id)
    q = (await storage.list_questions(parent.id))[0]
    await coordinator.aoe.action_started(q.id, "worker-openai", active_attempts(q)[0]["id"])
    proof = await storage.register_artifact(Artifact(task_id=q.id, created_by=SYSTEM_ACTOR,
        name="proof", storage_path="fixture", media_type="text/plain", sha256="0" * 64, size_bytes=0))
    finding = await storage.publish(q.id, SYSTEM_ACTOR, PublishRequest(
        kind="finding", body={"claim": "Verified flag from assigned target"}, idempotency_key="proof",
        artifact_refs=[ArtifactRef(artifact_id=proof.id, locator="stdout")]))
    q = await storage.get_task(q.id)
    await storage.update_agent(q.id, "worker-openai", metadata={"blackboard_cursor": q.blackboard_seq})
    body = FinalCandidateBody(conclusion=FLAG, rationale="Actual output", finding_ids=[finding.id])
    return cfg, storage, coordinator, parent, clock, q, body


def action(cfg, q, *, reply, command=None, agent="worker-openai", tool="Bash"):
    workspace = cfg.task_path(q.id, "workspaces", agent)
    workspace.mkdir(parents=True, exist_ok=True)
    journal = ActionJournal(cfg.task_path(q.id, "logs", agent),
        identity={"task_id": str(q.id), "agent_id": agent}, secret="", output_directory=workspace)
    return journal.record(str(uuid4()), tool, inputs={"command": command or
        f"curl -sS -X POST {URL} -d '{{\"unique_code\":\"q-1\",\"flag\":\"{FLAG}\"}}'"},
        result={"type": "tool_result", "content": {
            "stdout": reply if isinstance(reply, str) else json.dumps(reply), "stderr": ""}, "exit_code": 0},
        status="completed")


async def publish_file(cfg, storage, q, body):
    workspace = cfg.task_path(q.id, "workspaces", "worker-openai")
    (workspace / "final-candidate.json").write_text(body.model_dump_json())
    from tga3.blackboard import Blackboard

    importer = FilePublications(cfg, Blackboard(storage))
    return importer, (await importer.sync_final(q.id, "worker-openai"))[0]


async def test_missing_receipt_blocks_success_and_resends_stable_credentials(tmp_path):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    _, receipt = await publish_file(cfg, storage, q, body)
    assert receipt["status"] == "submission_required"
    path = cfg.task_path(q.id, "workspaces", "worker-openai") / ".tga3/submission.json"
    context = json.loads(path.read_text())
    assert context["submission"]["request"]["headers"]["BENCHMARK_TOKEN"] == "fixture-token"
    assert context["submission"]["request"]["url"] == URL
    assert receipt["submission_path"] == "/workspace/.tga3/submission.json"
    assert not any(e.kind == "final_candidate" for e in await storage.list_entries(q.id))
    assert not (cfg.task_path(q.id) / "final-candidate.json").exists()


async def test_real_matching_acceptance_imports_and_binds_action(tmp_path):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    saved = action(cfg, q, reply={"correct": True, "awarded": 300,
        "correct_flag_count": 1, "total_flag_count": 1})
    importer, receipt = await publish_file(cfg, storage, q, body)
    assert receipt["status"] == "imported"
    canonical = json.loads((cfg.task_path(q.id) / "final-candidate.json").read_text())
    assert canonical["submission_action_id"] == saved["action_id"]
    assert canonical["conclusion"] == FLAG
    assert await importer.sync_final(q.id, "worker-openai") == []


async def test_platform_wrong_answer_does_not_resend_credentials(tmp_path, monkeypatch):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    action(cfg, q, reply={"correct": False, "awarded": 0})
    from tga3.submissions import Submissions

    def forbidden(*args):
        raise AssertionError("Wrong flag must not trigger a credential resend")
    monkeypatch.setattr(Submissions, "write_context", forbidden)
    _, receipt = await publish_file(cfg, storage, q, body)
    assert receipt["status"] == "submission_rejected"
    assert not any(e.kind == "final_candidate" for e in await storage.list_entries(q.id))


@pytest.mark.parametrize("command,tool", [
    (f"curl {URL} -d 'q-1 HTB{{a-different-flag}}'", "Bash"),
    (f"curl {URL} -d 'different-question {FLAG}'", "Bash"),
    (f"curl {URL} -d '{{\"unique_code\":\"q-10\",\"flag\":\"{FLAG}\"}}'", "Bash"),
    (f"curl https://other.invalid/submit -d 'q-1 {FLAG}'", "Bash"),
    (f"curl {URL} -d 'q-1 {FLAG}'", "Write"),
])
async def test_unrelated_or_authored_receipt_is_not_acceptance(tmp_path, command, tool):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    action(cfg, q, reply={"correct": True}, command=command, tool=tool)
    _, receipt = await publish_file(cfg, storage, q, body)
    assert receipt["status"] == "submission_required"


async def test_partial_flags_are_not_a_completed_question(tmp_path):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    action(cfg, q, reply={"correct": True, "correct_flag_count": 1, "total_flag_count": 2})
    with pytest.raises(SubmissionRejected, match="not complete"):
        from tga3.submissions import Submissions
        Submissions(cfg).validate(q, body)


async def test_exact_duplicate_receipt_can_confirm_an_already_accepted_flag(tmp_path):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    saved = action(cfg, q, reply={"code": "duplicate", "message": "already correctly submitted"})
    _, receipt = await publish_file(cfg, storage, q,
        body.model_copy(update={"submission_action_id": saved["action_id"]}))
    assert receipt["status"] == "imported"


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
    action(cfg, q, reply={"correct": True, "correct_flag_count": 1, "total_flag_count": 1})
    _, receipt = await publish_file(cfg, storage, q, body)
    assert receipt["status"] == "imported"
    await c.aoe.tick(parent.id)
    assert (await storage.get_task(q.id)).metadata["status"] == "solved"
    assert not active_attempts(await storage.get_task(q.id))


@pytest.mark.parametrize("tool,command", [
    ("Read", "read output.txt"), ("Write", "save file"),
    ("Bash", f"echo '{FLAG}'"), ("Bash", "curl https://github.com/public/writeup"),
])
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


@pytest.mark.parametrize('reply', [
    'HTTP/1.1 500 Internal Server Error\n{"correct":true}',
    '{"error":{"correct":true}}',
])
async def test_http_error_or_nested_error_is_not_a_success_receipt(tmp_path, reply):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    action(cfg, q, reply=reply)
    _, receipt = await publish_file(cfg, storage, q, body)
    assert receipt['status'] == 'submission_required'


async def test_plain_native_command_output_and_peer_receipt_are_supported(tmp_path):
    cfg, storage, _, _, _, q, body = await fixture(tmp_path)
    saved = action(cfg, q, agent='worker-claude',
        reply='Process exited with code 0\nFinal output:\n{"correct":true}\nHTTP=200\n')
    _, receipt = await publish_file(cfg, storage, q, body)
    assert receipt['status'] == 'imported'
    canonical = json.loads((cfg.task_path(q.id)/'final-candidate.json').read_text())
    assert canonical['submission_action_id'] == saved['action_id']


async def test_grace_scan_is_cached_and_survives_runtime_recreation(tmp_path, monkeypatch):
    cfg, storage, c, parent, clock, q, _ = await fixture(tmp_path)
    original = active_attempts(q)[0]
    clock[0] = original['deadline'] - 20
    calls = []
    read = c.aoe.submissions._actions
    monkeypatch.setattr(c.aoe.submissions, '_actions', lambda *args: (calls.append(args), read(*args))[1])
    await c.aoe.tick(parent.id)
    await c.aoe.tick(parent.id)
    assert not calls  # No journal exists, no full-log reads.
    action(cfg, q, reply={'output':FLAG}, command='python3 solve.py')
    await c.aoe.tick(parent.id)
    assert len(calls) == 1
    held = active_attempts(await storage.get_task(q.id))[0]
    from tga3.coordinator import TaskCoordinator
    from tga3.docker_runtime import FakeContainerRuntime

    restored = TaskCoordinator(cfg, storage, FakeContainerRuntime())
    restored.aoe.clock = lambda: held['deadline'] - 10
    await restored.aoe.restore_paths()
    await restored.aoe._recover_attempts(parent.id)
    assert active_attempts(await storage.get_task(q.id))[0]['deadline'] > held['deadline']


async def test_aoe_cli_uses_the_runtime_deadline_not_a_fixed_cycle_timer(tmp_path, monkeypatch):
    from tga3.workers.cli_adapter import CodexAdapter

    for key,value in {'TGA3_WORKSPACE':str(tmp_path), 'TGA3_LOGS':str(tmp_path/'logs'),
        'TGA3_TASK_ID':str(uuid4()), 'TGA3_AGENT_ID':'worker-openai', 'TGA3_AGENT_ROLE':'worker',
        'TGA3_AGENT_RUNTIME':'codex_cli', 'TGA3_AOE_PARENT_ID':str(uuid4()),
        'TGA3_API_KEY':'fixture', 'TGA3_MODEL_NAME':'fixture', 'TGA3_PROVIDER_PROTOCOL':'openai_responses',
        'TGA3_CLI_TIMEOUT_SECONDS':'1800'}.items():
        monkeypatch.setenv(key,value)
    assert CodexAdapter().timeout is None


@pytest.mark.parametrize('same_origin', [True, False])
async def test_legacy_contract_uses_only_a_documented_url_and_same_origin_credentials(tmp_path, same_origin):
    cfg, storage, _, _, _, q, _ = await fixture(tmp_path)
    q = await storage.update_task(q.id, metadata={
        'submission_instructions':f'POST {URL} with JSON unique_code and flag'})
    from tga3.submissions import Submissions

    target={'start':{'url':URL if same_origin else 'https://other.invalid/start',
                     'headers':{'BENCHMARK_TOKEN':'legacy-fixture'}}}
    spec=Submissions.legacy_spec(q,target)
    if same_origin:
        assert spec.request.url == URL and spec.request.headers['BENCHMARK_TOKEN']=='legacy-fixture'
        assert spec.request.body == {'unique_code':'q-1','flag':'{flag}'}
    else:
        assert spec is None
