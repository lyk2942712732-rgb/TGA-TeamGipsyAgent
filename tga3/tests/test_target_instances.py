import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError
from test_aoe_runtime import final, setup

from tga3.aoe_models import AOEQuestionInput, active_attempts
from tga3.target_instances import TargetInstances, TargetSpec


def contract(key="q", limit=1):
    return {"mode": "managed", "quota_limit": limit,
            "start": {"url": "https://platform.test/start", "params": {"unique_code": key},
                      "headers": {"BENCHMARK_TOKEN": "fixture"}},
            "stop": {"url": "https://platform.test/close", "params": {"unique_code": key},
                     "headers": {"BENCHMARK_TOKEN": "fixture"}}}


async def batch(tmp_path, specifications, handler):
    cfg, storage, containers, coordinator, parent, clock = await setup(tmp_path)
    class Reports:
        def start_report(self, task_id):
            pass

        async def cancel(self, task_id):
            pass

    coordinator.aoe.automation = Reports()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    coordinator.aoe.targets = TargetInstances(cfg, clock=lambda: clock[0], client=client)
    await coordinator.aoe.register_questions(parent.id, [AOEQuestionInput(**s) for s in specifications])
    await coordinator.aoe.finish_collection(parent.id, len(specifications))
    questions = {q.metadata["source_key"]: q for q in await storage.list_questions(parent.id)}
    return cfg, storage, containers, coordinator, parent, clock, questions


def question(key, difficulty="easy", target=None):
    return {"source_key": key, "title": key, "description": "source supplied details",
            "difficulty": difficulty, "target": target or {"mode": "none"}}


def test_contract_rejects_missing_stop_and_non_http():
    with pytest.raises(ValidationError):
        TargetSpec(mode="managed", start={"url": "https://platform.test/start"})
    with pytest.raises(ValidationError):
        TargetSpec.model_validate({**contract(), "start": {"url": "file:///etc/passwd"}})
    assert AOEQuestionInput(source_key="legacy", title="legacy", description="legacy").target.mode == "unknown"


async def test_external_quota_dispatches_all_no_target_questions_in_existing_order(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(409, json={"message": "max active challenge instances reached (3)"})
    cfg, storage, containers, coord, parent, clock, qs = await batch(tmp_path, [
        question("managed", target=contract()),
        question("static", target={"mode": "static"}),
        question("offline-hard", "hard"), question("offline-medium", "medium")], handler)
    # Collection has finished; without the quota exception the easier fixed target would precede these.
    await asyncio.gather(coord.aoe.tick(parent.id), coord.aoe.tick(parent.id))
    assert [spec.task_id for spec in containers.launched] == [qs["offline-medium"].id, qs["offline-hard"].id]
    assert len(requests) == 1  # Account-wide backoff, not one retry loop per Worker/question.
    assert (await storage.get_task(qs["managed"].id)).metadata["attempts"] == []
    manifest = json.loads((cfg.task_path(qs["managed"].id) / "question.json").read_text())
    assert manifest["target"]["start"]["params"]["unique_code"] == "q"
    assert manifest["target_runtime"]["reason"] == "quota"
    assert "target" not in (await coord.aoe.snapshot(parent.id))["questions"][0]
    assert "target" in json.loads((cfg.task_path(parent.id) / "questions.json").read_text())["questions"][0]
    await coord.close()


async def test_local_quota_one_lease_shared_joint_and_solve_timeout_cleanup(tmp_path):
    requests = []
    def handler(request):
        requests.append((request.url.path, request.url.params.get("unique_code")))
        return httpx.Response(200, json={"container_addr": ["10.1.2.3:1337"]} if request.url.path == "/start"
                              else {"closed": True})
    cfg, storage, containers, coord, parent, clock, qs = await batch(tmp_path,
        [question("one", target=contract("one")), question("two", target=contract("two"))], handler)
    aoe = coord.aoe
    await aoe.tick(parent.id)
    assert len(containers.launched) == 1
    first = containers.launched[0]
    assert first.target_mode == "managed" and first.target_addresses == ("10.1.2.3:1337",)
    assert active_attempts(await storage.get_task(first.task_id))[0]["started_at"] is None
    await aoe.action_started(first.task_id, first.agent.agent_id, first.attempt_id)
    await final(storage, first.task_id)
    await aoe.tick(parent.id)
    assert requests[:3] == [("/start", "one"), ("/close", "one"), ("/start", "two")]
    second = containers.launched[-1]
    assert second.task_id == qs["two"].id
    # A new service object recovers the owned lease without starting the target a second time.
    fresh = TargetInstances(cfg, clock=lambda: clock[0], client=aoe.targets.client)
    assert await fresh.prepare(await storage.get_task(second.task_id))
    assert requests.count(("/start", "two")) == 1
    await aoe.action_started(second.task_id, second.agent.agent_id, second.attempt_id)
    clock[0] += 1201
    await aoe.tick(parent.id)
    assert requests.count(("/close", "two")) == 1
    assert requests.count(("/start", "two")) == 2  # Next independent attempt gets its own live lease.
    await aoe.stop_children(parent.id, cancelled=True)
    assert requests.count(("/close", "two")) == 2
    assert all(not record.get("owned") for record in aoe.targets.records.values())
    await coord.close()


async def test_failed_close_still_occupies_quota_and_retries_after_restart(tmp_path):
    fail = [True]
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(200, json={"container_addr": ["target:1"]})
        return httpx.Response(503, json={"message": "busy"}) if fail[0] else httpx.Response(200, json={"closed": True})
    cfg, storage, containers, coord, parent, clock, qs = await batch(tmp_path,
        [question("one", target=contract("one")), question("two", target=contract("two"))], handler)
    await coord.aoe.tick(parent.id)
    first = containers.launched[0]
    await final(storage, first.task_id)
    await coord.aoe.tick(parent.id)
    assert len(containers.launched) == 1
    assert coord.aoe.targets.state(first.task_id)["state"] == "closing"
    restored = TargetInstances(cfg, clock=lambda: clock[0], client=coord.aoe.targets.client)
    fail[0] = False
    clock[0] += 11
    await restored.reconcile(await storage.list_questions())
    assert restored.state(first.task_id)["state"] == "released"
    coord.aoe.targets = restored
    await coord.aoe.tick(parent.id)
    assert len(containers.launched) == 2
    await coord.aoe.stop_children(parent.id, cancelled=True)
    await coord.close()


async def test_async_start_and_transport_timeout_do_not_launch_or_start_budget(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/start":
            raise httpx.ReadTimeout("fixture", request=request)
        if request.url.path == "/status":
            return httpx.Response(200, json={"data": {"addresses": ["target:42"]}})
        return httpx.Response(200, json={"closed": True})
    spec = {**contract(), "status": {"method": "GET", "url": "https://platform.test/status"},
            "addresses_path": "data.addresses"}
    _, storage, containers, coord, parent, clock, qs = await batch(tmp_path, [question("one", target=spec)], handler)
    await coord.aoe.tick(parent.id)
    assert not containers.launched and not (await storage.get_task(qs["one"].id)).metadata["attempts"]
    clock[0] += 11
    await coord.aoe.tick(parent.id)
    assert len(containers.launched) == 1 and calls == ["/start", "/status"]
    assert active_attempts(await storage.get_task(qs["one"].id))[0]["deadline"] is None
    await coord.aoe.stop_children(parent.id, cancelled=True)
    await coord.close()


async def test_joint_participants_use_one_instance_and_expiry_closes_once(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"container_addr": ["target:9"]} if request.url.path == "/start"
                              else {"closed": True})
    _, storage, containers, coord, parent, clock, qs = await batch(tmp_path,
        [question("one", target=contract())], handler)
    for _ in range(2):
        await coord.aoe.tick(parent.id)
        spec = containers.launched[-1]
        await coord.aoe.action_started(spec.task_id, spec.agent.agent_id, spec.attempt_id)
        clock[0] += 1201
    await coord.aoe.tick(parent.id)
    joint = containers.launched[-2:]
    assert len({s.agent.agent_id for s in joint}) == 2
    assert len({s.task_id for s in joint}) == 1
    assert calls == ["/start", "/close", "/start", "/close", "/start"]
    assert all(s.target_addresses == ("target:9",) for s in joint)
    await coord.aoe.action_started(joint[0].task_id, joint[0].agent.agent_id, joint[0].attempt_id)
    clock[0] += 1201
    await coord.aoe.tick(parent.id)
    assert calls[-1] == "/close" and calls.count("/close") == 3
    assert (await storage.get_task(qs["one"].id)).metadata["status"] == "abandoned"
    await coord.close()


async def test_account_quota_is_shared_across_tasks_and_cleanup_is_owned(tmp_path):
    from uuid import uuid4

    from tga3.domain import TaskRun

    calls = []
    def handler(request):
        calls.append((request.url.path, request.url.params.get("unique_code")))
        return httpx.Response(200, json={"container_addr": ["target:1"]} if request.url.path == "/start"
                              else {"closed": True})
    cfg, _, _, coord, _, _, _ = await batch(tmp_path, [question("offline")], handler)
    first = TaskRun(id=uuid4(), title="first", scene_id="aoe", parent_task_id=uuid4(),
                    metadata={"target": contract("first")})
    second = TaskRun(id=uuid4(), title="second", scene_id="aoe", parent_task_id=uuid4(),
                     metadata={"target": contract("second")})
    result = await asyncio.gather(coord.aoe.targets.prepare(first), coord.aoe.targets.prepare(second))
    assert result == [True, False] and calls == [("/start", "first")]
    assert await coord.aoe.targets.release_parent(second.parent_task_id)
    assert calls == [("/start", "first")]  # Never close the other task's leased instance.
    await coord.aoe.targets.release(first.id)
    assert await coord.aoe.targets.prepare(second)
    assert calls == [("/start", "first"), ("/close", "first"), ("/start", "second")]
    await coord.aoe.targets.release(second.id)
    await coord.close()
