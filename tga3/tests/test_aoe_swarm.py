import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from test_aoe_runtime import final, setup

from tga3.aoe_models import AOEQuestionInput, active_attempts


async def batch(tmp_path, count=1, *, complete=True):
    cfg, storage, runtime, c, parent, clock = await setup(tmp_path, four_workers=True)
    await c.aoe.register_questions(parent.id, [
        AOEQuestionInput(source_key=str(i), title=str(i), description='original', difficulty='hard')
        for i in range(count)])
    if complete:
        await c.aoe.finish_collection(parent.id, count)
    await storage.update_task(parent.id, metadata={
        'aoe_attempt_budgets': {'first': 60, 'second': 90, 'joint': 180}})
    questions = await storage.list_questions(parent.id)
    return cfg, storage, runtime, c, parent, clock, questions


@pytest.mark.parametrize('complete', [True, False])
async def test_last_question_starts_four_even_during_collection(tmp_path, complete):
    cfg, storage, runtime, c, parent, clock, (q,) = await batch(tmp_path, complete=complete)
    await asyncio.gather(*(c.aoe.tick(parent.id) for _ in range(6)))
    assert len(runtime.launched) == 4
    assert {s.agent.agent_id for s in runtime.launched} == set(cfg.worker_agent_ids)
    assert all(s.fresh_session for s in runtime.launched)
    assert (await storage.get_task(parent.id)).metadata['aoe_phase'] == 'swarm'
    attempts = active_attempts(await storage.get_task(q.id))
    assert {a['phase'] for a in attempts} == {'swarm'}
    assert all(a['started_at'] is None and a['deadline'] is None for a in attempts)
    # Preparation and repeated ticks must not consume or multiply the budget.
    clock[0] = 1000
    await c.aoe.action_started(q.id, attempts[2]['agent_id'], attempts[2]['id'])
    clock[0] = 1010
    await c.aoe.action_started(q.id, attempts[0]['agent_id'], attempts[0]['id'])
    current = await storage.get_task(q.id)
    assert {a['started_at'] for a in active_attempts(current)} == {1000}
    assert {a['deadline'] for a in active_attempts(current)} == {1180}
    assert current.metadata['swarm']['deadline'] == 1180
    await c.aoe.tick(parent.id)
    assert len(runtime.launched) == 4
    clock[0] = 1180
    await c.aoe.tick(parent.id)
    assert (await storage.get_task(q.id)).metadata['status'] == 'abandoned'
    assert not active_attempts(await storage.get_task(q.id))
    assert {agent for ident, agent in runtime.stopped if ident == q.id} == set(cfg.worker_agent_ids)


@pytest.mark.parametrize('stage', ['solo', 'joint'])
@pytest.mark.parametrize('returning_index', range(4))
async def test_promotes_live_workers_without_restart_and_resumes_previous_workers(tmp_path, stage, returning_index):
    cfg, storage, runtime, c, parent, clock, (q, other) = await batch(tmp_path, 2)
    workers = cfg.worker_agent_ids
    first, second, third, fourth = workers[returning_index:] + workers[:returning_index]
    # Create genuine previous attempts/session pointers through normal paths.
    q = await c.aoe._new_attempts(parent.id, q, (first,), 'solo')
    await c.aoe.action_started(q.id, first, active_attempts(q)[0]['id'])
    run = await storage.get_agent(q.id, first)
    path = cfg.task_path(q.id, 'workspaces', first) / '.tga3' / f'{run.sdk}-session.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'session_id': 'saved-first-session'}))
    q = await c.aoe._finish_attempts(await storage.get_task(q.id), {first}, 'timeout')
    q = await c.aoe._new_attempts(parent.id, q, (second,), 'solo')
    if stage == 'joint':
        q = await c.aoe._finish_attempts(q, {second}, 'timeout')
        q = await c.aoe._new_attempts(parent.id, q, (third, fourth), 'joint')
    live = active_attempts(q)
    await c.aoe.action_started(q.id, live[0]['agent_id'], live[0]['id'])
    previous_stops = list(runtime.stopped)
    prior_launches = len(runtime.launched)
    notify = c.gateway.notify = AsyncMock(wraps=c.gateway.notify)
    clock[0] = 120
    await final(storage, other.id)
    await c.aoe.tick(parent.id)
    current = await storage.get_task(q.id)
    swarm = active_attempts(current)
    assert len(swarm) == 4
    assert {a['deadline'] for a in swarm} == {300}
    assert {a['id'] for a in live} <= {a['id'] for a in swarm}
    assert {a['started_at'] for a in swarm} == {120}
    for attempt in live:
        notify.assert_any_await(q.id, attempt['agent_id'], 'session.deadline',
                               {'attempt_id': attempt['id'], 'deadline': 300})
    assert list(runtime.stopped) == previous_stops
    joined = runtime.launched[prior_launches:]
    assert len(joined) == 4 - len(live)
    assert not any(s.agent.agent_id in {a['agent_id'] for a in live} for s in joined)
    assert not next(s for s in joined if s.agent.agent_id == first).fresh_session
    assert json.loads(path.read_text())['session_id'] == 'saved-first-session'
    assert all(a['phase'] == 'swarm' for a in swarm)
    assert next(a for a in swarm if a['id'] == live[0]['id'])['previous_phase'] == stage
    clock[0] = 150
    await c.aoe.tick(parent.id)
    assert len(runtime.launched) == prior_launches + len(joined)
    assert {a['deadline'] for a in active_attempts(await storage.get_task(q.id))} == {300}


async def test_final_candidate_order_cannot_delay_swarm_or_regrant_its_budget(tmp_path):
    cfg, storage, runtime, c, parent, clock, (q, other) = await batch(tmp_path, 2)
    # q appears before the other question's final candidate in storage order.
    q = await c.aoe._new_attempts(parent.id, q, (cfg.worker_agent_ids[0],), 'solo')
    live = active_attempts(q)[0]
    await c.aoe.action_started(q.id, live['agent_id'], live['id'])
    clock[0] = 160  # The old first-attempt timer expires exactly at the transition.
    await final(storage, other.id)
    await c.aoe.tick(parent.id)
    current = await storage.get_task(q.id)
    assert {a['deadline'] for a in active_attempts(current)} == {340}
    assert live['id'] in {a['id'] for a in active_attempts(current)}
    assert (q.id, live['agent_id']) not in runtime.stopped
    clock[0] = 340
    await c.aoe.tick(parent.id)
    assert (await storage.get_task(q.id)).metadata['status'] == 'abandoned'
    assert len(runtime.launched) == 4


async def test_waits_for_scout_handoff_and_leaves_normal_multi_question_schedule(tmp_path):
    cfg, storage, runtime, c, parent, _, (q, other) = await batch(tmp_path, 2)
    await c.aoe.tick(parent.id)
    assert len(runtime.launched) == 2
    assert all(s.fresh_session for s in runtime.launched)
    assert not (await storage.get_task(q.id)).metadata.get('swarm')
    assert not (await storage.get_task(other.id)).metadata.get('swarm')
    await c.aoe._finish_attempts(await storage.get_task(q.id), {runtime.launched[0].agent.agent_id}, 'timeout')
    await storage.update_task(q.id, metadata={'scout_ready': False})
    await final(storage, other.id)
    count = len(runtime.launched)
    await c.aoe.tick(parent.id)
    assert len(runtime.launched) == count
    await storage.update_task(q.id, metadata={'scout_ready': True})
    await c.aoe.tick(parent.id)
    assert len(active_attempts(await storage.get_task(q.id))) == 4


async def test_solution_stops_all_four_and_never_relaunches(tmp_path):
    cfg, storage, runtime, c, parent, _, (q,) = await batch(tmp_path)
    await c.aoe.tick(parent.id)
    await final(storage, q.id)
    await c.aoe.tick(parent.id)
    await c.aoe.tick(parent.id)
    current = await storage.get_task(q.id)
    assert current.metadata['status'] == 'solved'
    assert {a['outcome'] for a in current.metadata['attempts']} == {'solved'}
    assert len(runtime.launched) == 4
    assert {agent for ident, agent in runtime.stopped if ident == q.id} == set(cfg.worker_agent_ids)
    assert c.aoe.automation.questions == [q.id]


async def test_recovery_and_late_resume_keep_original_shared_deadline(tmp_path):
    cfg, storage, runtime, c, parent, clock, (q,) = await batch(tmp_path)
    await c.aoe.tick(parent.id)
    attempts = active_attempts(await storage.get_task(q.id))
    await c.aoe.action_started(q.id, attempts[0]['agent_id'], attempts[0]['id'])
    deadline = 280
    # Controller recovery resumes existing attempts, never makes fresh sessions.
    clock[0] = 130
    await c.aoe._recover_attempts(parent.id)
    assert all(not s.fresh_session and s.attempt_deadline == deadline for s in runtime.launched[-4:])
    returning = attempts[2]['agent_id']
    await c.aoe._finish_attempts(await storage.get_task(q.id), {returning}, 'interrupted')
    await storage.update_agent(parent.id, returning, metadata={'aoe_paused': True})
    await c.aoe.tick(parent.id)
    assert len(active_attempts(await storage.get_task(q.id))) == 3
    clock[0] = 150
    await storage.update_agent(parent.id, returning, metadata={'aoe_paused': False})
    await c.aoe.tick(parent.id)
    last = runtime.launched[-1]
    assert last.agent.agent_id == returning and not last.fresh_session
    assert last.attempt_deadline == deadline
    assert {a['deadline'] for a in active_attempts(await storage.get_task(q.id))} == {deadline}
