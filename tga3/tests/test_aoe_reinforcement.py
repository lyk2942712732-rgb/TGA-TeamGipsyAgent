import asyncio

import pytest
from test_aoe_runtime import final, setup

from tga3.aoe_models import (
    AOEQuestionInput,
    active_attempts,
    attempt_budget_seconds,
    attempted_by,
    first_success_statistics,
    joint_workers,
    rescue_after,
    solo_attempts,
)
from tga3.domain import RunState


async def scenario(tmp_path, mean=120):
    cfg, storage, runtime, c, parent, clock = await setup(tmp_path, four_workers=True)
    a, b, cc, d = cfg.worker_agent_ids
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key=name, title=name, description='original', difficulty='easy')
        for name in ('sample', 'slow', 'waiting')])
    await c.aoe.finish_collection(parent.id, 3)
    sample, slow, waiting = await storage.list_questions(parent.id)
    sample = await storage.update_task(sample.id, state=RunState.COMPLETED, metadata={'status': 'solved', 'attempts': [
        {'id': 'sample-first', 'phase': 'solo', 'agent_id': a, 'started_at': 0,
         'finished_at': mean, 'deadline': mean + 1, 'outcome': 'solved'}]})
    # A second unfinished question prevents swarm and is already past solo rounds.
    await storage.update_task(waiting.id, metadata={'attempts': [
        {'id': f'waiting-{i}', 'phase': 'solo', 'agent_id': agent, 'outcome': 'timeout'}
        for i, agent in enumerate((cc, d))]})
    slow = await c.aoe._new_attempts(parent.id, slow, (a,), 'solo')
    attempt = active_attempts(slow)[0]
    await c.aoe.action_started(slow.id, a, attempt['id'])
    return cfg, storage, runtime, c, parent, clock, sample, slow, waiting


@pytest.mark.parametrize('mean,elapsed,seeking', [(120, 600, False), (120, 601, True),
    (500, 650, False), (500, 680, False), (500, 681, True)])
async def test_rescue_requires_both_strict_time_thresholds(tmp_path, mean, elapsed, seeking):
    cfg, storage, runtime, c, parent, clock, _, q, _ = await scenario(tmp_path, mean)
    clock[0] = 100 + elapsed
    # All idle Workers are manually paused: requesting help must not steal one.
    for agent in cfg.worker_agent_ids[1:]:
        await storage.update_agent(parent.id, agent, metadata={'aoe_paused': True})
    await c.aoe.tick(parent.id)
    current = await storage.get_task(q.id)
    assert bool(current.metadata.get('rescue')) == seeking
    assert len(runtime.launched) == 1
    assert not runtime.stopped
    assert not current.metadata.get('reinforcement')


async def test_idle_helper_joins_once_without_restarting_owner_or_refreshing_budget(tmp_path):
    cfg, storage, runtime, c, parent, clock, _, q, _ = await scenario(tmp_path)
    original = active_attempts(await storage.get_task(q.id))[0]
    clock[0] = 701
    await asyncio.gather(*(c.aoe.tick(parent.id) for _ in range(6)))
    current = await storage.get_task(q.id)
    first, helper = active_attempts(current)
    assert first == original
    assert helper['agent_id'] == cfg.worker_agent_ids[1]
    assert helper['phase'] == 'reinforcement' and helper['first_attempt_id'] == first['id']
    assert helper['started_at'] == 701 and helper['deadline'] == first['deadline'] == 1300
    assert current.metadata['rescue']['state'] == 'reinforced'
    assert len(runtime.launched) == 2 and not runtime.stopped
    assert runtime.launched[-1].fresh_session
    assert runtime.launched[-1].evidence_agents == (first['agent_id'],)
    assert attempted_by(current) == set(cfg.worker_agent_ids[:2])
    assert len(solo_attempts(current)) == 1


async def test_regular_work_has_priority_over_reinforcement(tmp_path):
    cfg, storage, runtime, c, parent, clock, _, q, _ = await scenario(tmp_path)
    # In-flight collection can still register new work, even while an old task seeks help.
    await storage.update_task(parent.id, metadata={'collection_complete': False})
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key='fresh', title='fresh', description='original', difficulty='hard')])
    await c.aoe.finish_collection(parent.id, 4)
    clock[0] = 701
    await c.aoe.tick(parent.id)
    questions = await storage.list_questions(parent.id)
    fresh = next(item for item in questions if item.title == 'fresh')
    assert runtime.launched[1].task_id == fresh.id
    assert runtime.launched[1].agent.agent_id == cfg.worker_agent_ids[1]
    helper = next(a for a in active_attempts(await storage.get_task(q.id)) if a['phase'] == 'reinforcement')
    assert helper['agent_id'] == cfg.worker_agent_ids[2]


async def test_target_reserved_worker_is_not_borrowed_for_help(tmp_path, monkeypatch):
    cfg, storage, runtime, c, parent, clock, _, q, waiting = await scenario(tmp_path)
    reserved = cfg.worker_agent_ids[1]
    await storage.update_task(waiting.id, metadata={'attempts': []})
    c.aoe._target_claims[waiting.id] = (reserved,)
    prepare = c.aoe._prepare_target

    async def pending(question, agents):
        return False if question.id == waiting.id else await prepare(question, agents)

    monkeypatch.setattr(c.aoe, '_prepare_target', pending)
    clock[0] = 701
    await c.aoe.tick(parent.id)
    helper = next(a for a in active_attempts(await storage.get_task(q.id)) if a['phase'] == 'reinforcement')
    assert helper['agent_id'] != reserved
    assert len(runtime.launched) == 2


async def test_expiry_during_target_preparation_skips_help_without_renewing_budget(tmp_path, monkeypatch):
    cfg, storage, runtime, c, parent, clock, _, q, _ = await scenario(tmp_path)
    clock[0] = 1299

    async def prepare(_question, _agents):
        clock[0] = 1300
        return True

    monkeypatch.setattr(c.aoe, '_prepare_target', prepare)
    await c.aoe.tick(parent.id)
    current = await storage.get_task(q.id)
    assert len(runtime.launched) == 1
    assert not current.metadata.get('reinforcement')
    assert active_attempts(current)[0]['deadline'] == 1300
    assert (await storage.get_task(parent.id)).state != RunState.FAILED


async def test_reinforced_first_then_second_then_joint_uses_helper_and_unused_worker(tmp_path):
    cfg, storage, runtime, c, parent, clock, _, q, _ = await scenario(tmp_path)
    a, b, cc, d = cfg.worker_agent_ids
    await storage.update_task(parent.id, metadata={
        'aoe_attempt_budgets': {'first': 1200, 'second': 90, 'joint': 180}})
    clock[0] = 701
    await c.aoe.tick(parent.id)
    clock[0] = 1300
    await c.aoe.tick(parent.id)
    current = await storage.get_task(q.id)
    second, = active_attempts(current)
    assert second['phase'] == 'solo' and second['agent_id'] == cc
    assert {item['agent_id'] for item in current.metadata['attempts'] if item.get('outcome') == 'timeout'} == {a, b}
    await c.aoe.action_started(q.id, cc, second['id'])
    assert active_attempts(await storage.get_task(q.id))[0]['deadline'] == 1390
    assert joint_workers(await storage.get_task(q.id), cfg.worker_agent_ids) == (b, d)
    clock[0] = 1390
    await c.aoe.tick(parent.id)
    joint = active_attempts(await storage.get_task(q.id))
    assert {item['agent_id'] for item in joint} == {b, d}
    assert all(item['phase'] == 'joint' for item in joint)
    assert not next(s for s in runtime.launched[-2:] if s.agent.agent_id == b).fresh_session
    assert next(s for s in runtime.launched[-2:] if s.agent.agent_id == d).fresh_session


def test_reinforcement_is_first_budget_not_second():
    assert attempt_budget_seconds({'aoe_attempt_budgets': {'first': 1200, 'second': 90, 'joint': 180}},
                                  [], {'phase': 'reinforcement'}) == 1200


async def test_reinforcement_promotes_to_swarm_and_abandons_after_one_joint_budget(tmp_path):
    cfg, storage, runtime, c, parent, clock, _, q, waiting = await scenario(tmp_path)
    clock[0] = 701
    await c.aoe.tick(parent.id)
    live = active_attempts(await storage.get_task(q.id))
    await final(storage, waiting.id)
    clock[0] = 750
    await c.aoe.tick(parent.id)
    current = await storage.get_task(q.id)
    assert len(active_attempts(current)) == 4
    assert {item['id'] for item in live} <= {item['id'] for item in active_attempts(current)}
    assert {item['deadline'] for item in active_attempts(current)} == {1950}
    assert not any((q.id, item['agent_id']) in runtime.stopped for item in live)
    clock[0] = 1950
    await c.aoe.tick(parent.id)
    current = await storage.get_task(q.id)
    assert current.metadata['status'] == 'abandoned'
    assert current.metadata['finish_reason'] == 'joint_failed'
    assert len([spec for spec in runtime.launched if spec.task_id == q.id]) == 4


async def test_no_baseline_no_rescue_and_second_attempt_never_requests_help(tmp_path):
    cfg, storage, runtime, c, parent, clock, sample, q, _ = await scenario(tmp_path)
    sample = await storage.update_task(sample.id, metadata={'difficulty': 'hard'})
    clock[0] = 701
    await c.aoe.tick(parent.id)
    assert not (await storage.get_task(q.id)).metadata.get('rescue')
    await c.aoe._finish_attempts(await storage.get_task(q.id), {cfg.worker_agent_ids[0]}, 'timeout')
    sample_attempts = sample.metadata['attempts']
    sample_attempts[0] = {**sample_attempts[0], 'agent_id': cfg.worker_agent_ids[1]}
    await storage.update_task(sample.id, metadata={'difficulty': 'easy', 'attempts': sample_attempts})
    q = await c.aoe._new_attempts(parent.id, await storage.get_task(q.id), (cfg.worker_agent_ids[1],), 'solo')
    second = active_attempts(q)[0]
    await c.aoe.action_started(q.id, second['agent_id'], second['id'])
    clock[0] = 1402
    await c.aoe.tick(parent.id)
    assert not (await storage.get_task(q.id)).metadata.get('rescue')


async def test_first_success_statistics_exclude_other_rounds_and_track_reinforcement(tmp_path):
    cfg, storage, _, c, parent, clock, sample, q, _ = await scenario(tmp_path)
    clock[0] = 701
    await c.aoe.tick(parent.id)
    clock[0] = 801
    await final(storage, q.id, agent_id=cfg.worker_agent_ids[1])
    await c.aoe.tick(parent.id)
    statistics = first_success_statistics(await storage.list_questions(parent.id))
    a = next(row for row in statistics if row['agent_id'] == cfg.worker_agent_ids[0])
    b = next(row for row in statistics if row['agent_id'] == cfg.worker_agent_ids[1])
    assert a['solved_count'] == 2 and a['average_seconds'] == (120 + 701) / 2
    assert b['solved_count'] == 1 and b['average_seconds'] == 100
    assert rescue_after(await storage.get_task(q.id), statistics) is None


@pytest.mark.parametrize('solver_index', [0, 1])
async def test_reinforced_owner_always_receives_credit_but_helper_only_if_actual_solver(tmp_path, solver_index):
    cfg, storage, _, c, parent, clock, _, q, _ = await scenario(tmp_path)
    clock[0] = 701
    await c.aoe.tick(parent.id)
    clock[0] = 801
    solver = cfg.worker_agent_ids[solver_index]
    await final(storage, q.id, agent_id=solver)
    await c.aoe.tick(parent.id)
    current = await storage.get_task(q.id)
    first = next(a for a in current.metadata['attempts'] if a['phase'] == 'solo')
    helper = next(a for a in current.metadata['attempts'] if a['phase'] == 'reinforcement')
    assert first['success_credit'] is True
    assert helper['success_credit'] is (solver_index == 1)
    assert current.metadata['solver_agent_id'] == solver
    statistics = first_success_statistics(await storage.list_questions(parent.id))
    assert any(row['agent_id'] == cfg.worker_agent_ids[0] and row['solved_count'] == 2 for row in statistics)
    assert any(row['agent_id'] == cfg.worker_agent_ids[1] for row in statistics) is (solver_index == 1)


@pytest.mark.parametrize('later_phase', ['solo', 'joint', 'swarm'])
async def test_statistics_do_not_count_later_round_or_swarm_success(tmp_path, later_phase):
    _, storage, _, _, _, _, sample, _, _ = await scenario(tmp_path)
    attempts = [{**sample.metadata['attempts'][0], 'outcome': 'timeout'}]
    attempts.append({**attempts[0], 'id': 'later-success', 'phase': later_phase,
                     'outcome': 'solved', 'started_at': 200, 'finished_at': 300})
    sample = await storage.update_task(sample.id, metadata={'attempts': attempts})
    assert first_success_statistics([sample]) == []
