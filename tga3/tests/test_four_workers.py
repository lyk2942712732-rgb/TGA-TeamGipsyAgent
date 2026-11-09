import asyncio
from itertools import combinations

import pytest
from test_aoe_runtime import setup

from tga3.aoe_models import AOEQuestionInput, active_attempts, joint_workers
from tga3.errors import ConflictError


async def test_four_workers_claim_four_distinct_questions_under_concurrent_ticks(tmp_path):
    cfg, storage, runtime, c, parent, _ = await setup(tmp_path, four_workers=True)
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(source_key=str(i), title=str(i),
                                             description='original', difficulty='easy') for i in range(6)])
    await asyncio.gather(*(c.aoe.tick(parent.id) for _ in range(8)))
    assert len(runtime.launched) == 4
    assert {s.agent.agent_id for s in runtime.launched} == set(cfg.worker_agent_ids)
    assert len({s.task_id for s in runtime.launched}) == 4
    assert all(len(active_attempts(q)) <= 1 for q in await storage.list_questions(parent.id))


async def test_second_failure_hands_joint_to_other_two_with_shared_first_action_budget(tmp_path):
    cfg, storage, runtime, c, parent, clock = await setup(tmp_path, four_workers=True)
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(source_key='one', title='one', description='original')])
    await c.aoe.finish_collection(parent.id, 1)
    question = (await storage.list_questions(parent.id))[0]
    first_two = []
    for turn in range(2):
        await c.aoe.tick(parent.id)
        spec = runtime.launched[-1]
        first_two.append(spec.agent.agent_id)
        assert len(active_attempts(await storage.get_task(question.id))) == 1
        await c.aoe.action_started(question.id, spec.agent.agent_id, spec.attempt_id)
        clock[0] += 1200
    # Paused participants from failed solo attempts must not block the fresh pair.
    await storage.update_agent(parent.id, first_two[0], metadata={'aoe_paused': True})
    await c.aoe.tick(parent.id)
    current = await storage.get_task(question.id)
    joint = active_attempts(current)
    assert len(joint) == 2 and all(a['phase'] == 'joint' for a in joint)
    assert {a['agent_id'] for a in joint} == set(cfg.worker_agent_ids)-set(first_two)
    assert set((await c.aoe.question_view(current))['attempted_by']) == set(cfg.worker_agent_ids)
    assert all(s.fresh_session for s in runtime.launched[-2:])
    assert all(set(s.evidence_agents) == set(first_two) for s in runtime.launched[-2:])
    assert all(a['started_at'] is None for a in joint)
    clock[0] += 5
    await c.aoe.action_started(question.id, joint[0]['agent_id'], joint[0]['id'])
    joint = active_attempts(await storage.get_task(question.id))
    assert {a['started_at'] for a in joint} == {clock[0]}
    assert {a['deadline'] for a in joint} == {clock[0]+1200}
    clock[0] += 1200
    await c.aoe.tick(parent.id)
    assert (await storage.get_task(question.id)).metadata['status'] == 'abandoned'
    assert len(runtime.launched) == 4


@pytest.mark.parametrize('tried', list(combinations(['worker-openai','worker-claude','worker-opencode','worker-qwen'], 2)))
async def test_joint_pair_is_the_exact_complement_of_any_two_solo_workers(tmp_path, tried):
    cfg, storage, _, c, parent, _ = await setup(tmp_path, four_workers=True)
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(source_key='one', title='one', description='original')])
    question = (await storage.list_questions(parent.id))[0]
    question = await storage.update_task(question.id, metadata={'attempts': [
        {'id': str(i), 'phase': 'solo', 'agent_id': worker, 'outcome': 'timeout'} for i,worker in enumerate(tried)]})
    assert set(joint_workers(question, cfg.worker_agent_ids)) == set(cfg.worker_agent_ids)-set(tried)
    with pytest.raises(ConflictError, match='two Workers who have not tried'):
        await c.aoe._new_attempts(parent.id, question, tried, 'joint')
    with pytest.raises(ConflictError, match='at most two solo'):
        await c.aoe._new_attempts(parent.id, question, (joint_workers(question,cfg.worker_agent_ids)[0],), 'solo')
