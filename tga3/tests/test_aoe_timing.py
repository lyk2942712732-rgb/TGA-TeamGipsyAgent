from test_aoe_runtime import setup

from tga3.aoe_models import AOEQuestionInput, active_attempts


async def running(tmp_path, *, four_workers=False):
    cfg, storage, runtime, c, parent, clock = await setup(tmp_path, four_workers=four_workers)
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(source_key='q', title='q', description='original')])
    q, = await storage.list_questions(parent.id)
    q = await c.aoe._new_attempts(parent.id, q, (cfg.worker_agent_ids[0],), 'solo')
    a, = active_attempts(q)
    await c.aoe.action_started(q.id, a['agent_id'], a['id'])
    return storage, runtime, c, clock, await storage.get_task(q.id), a['agent_id']


async def test_acknowledged_pause_resume_records_time_without_changing_budget(tmp_path):
    storage, _, c, clock, q, agent = await running(tmp_path, four_workers=True)
    original = active_attempts(q)[0]
    clock[0] = 200
    await c.handle_agent_event(q.id, agent, 'agent.status', {'state': 'paused'})
    clock[0] = 250
    await c.handle_agent_event(q.id, agent, 'agent.status', {'state': 'paused'})
    clock[0] = 400
    await c.handle_agent_event(q.id, agent, 'agent.status', {'state': 'running'})
    await c.handle_agent_event(q.id, agent, 'agent.status', {'state': 'running'})
    current = active_attempts(await storage.get_task(q.id))[0]
    assert current['pauses'] == [{'start': 200, 'end': 400}]
    assert current['started_at'] == original['started_at'] == 100
    assert current['deadline'] == original['deadline'] == 1300


async def test_shutdown_latency_is_not_counted_as_solving_time(tmp_path, monkeypatch):
    storage, runtime, c, clock, q, agent = await running(tmp_path)
    stop = runtime.stop

    async def slow_stop(*args):
        clock[0] += 30
        await stop(*args)

    monkeypatch.setattr(runtime, 'stop', slow_stop)
    clock[0] = 500
    current = await c.aoe._finish_attempts(q, {agent}, 'timeout')
    assert current.metadata['attempts'][0]['finished_at'] == 500
    assert clock[0] == 530


async def test_pause_before_swarm_survives_budget_refresh(tmp_path):
    storage, _, c, clock, q, agent = await running(tmp_path, four_workers=True)
    clock[0] = 200
    await c.aoe.record_pause(q.id, agent, paused=True)
    clock[0] = 300
    parent = await storage.get_task(q.parent_task_id)
    slots = {run.agent_id: run for run in await storage.list_agents(parent.id)}
    current = await c.aoe._ensure_swarm(parent, await storage.get_task(q.id),
                                      tuple(c.config.worker_agent_ids), slots, set())
    attempt = next(a for a in active_attempts(current) if a['agent_id'] == agent)
    assert attempt['previous_started_at'] == 100
    assert attempt['started_at'] == 300
    assert attempt['pauses'] == [{'start': 200, 'end': None}]
    view = await c.aoe.question_view(current)
    assert view['swarm']['entered_at'] == 300
