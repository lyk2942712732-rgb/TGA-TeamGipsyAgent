"""Opt-in integration check against a disposable PostgreSQL database."""
import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from test_aoe_runtime import setup

from tga3.aoe_models import AOEQuestionInput
from tga3.coordinator import TaskCoordinator
from tga3.domain import RunState
from tga3.storage import PostgresStorage


@pytest.mark.parametrize("total", [40, 63])
async def test_real_postgres_stable_tick_has_bounded_reads_and_no_updates(tmp_path, total):
    dsn = os.environ.get("TGA3_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TGA3_TEST_POSTGRES_DSN must name a disposable test database")
    asyncpg = pytest.importorskip("asyncpg")
    admin = await asyncpg.connect(dsn)
    schema = "r04_" + uuid4().hex
    pool = c = None
    statements = []

    async def initialize(connection):
        connection.add_query_logger(lambda query: statements.append(query.query))

    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}"')
        await admin.execute((Path(__file__).parents[1] / "schema.sql").read_text())
        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=3, init=initialize,
                                        server_settings={"search_path": schema})
        storage = PostgresStorage(pool)
        cfg, memory, containers, previous, parent, _ = await setup(tmp_path)
        await previous.close()
        parent = await storage.create_task(parent.title, "aoe", parent.id, metadata=parent.metadata)
        await storage.update_task(parent.id, state=RunState.SCOUTING)
        for run in await memory.list_agents(parent.id):
            await storage.upsert_agent(run)
        c = TaskCoordinator(cfg, storage, containers)
        await c.aoe.register_questions(parent.id, [AOEQuestionInput(
            source_key=str(i), title=str(i), description="isolated fixture", difficulty="easy",
            target={"mode": "none"}) for i in range(total)])
        await c.aoe.finish_collection(parent.id, total)
        await c.aoe.tick(parent.id)
        await c.aoe.tick(parent.id)
        await asyncio.sleep(0)
        statements.clear()
        await c.aoe.tick(parent.id)
        await asyncio.sleep(0)
        queries = [q for q in statements if any(table in q for table in
                                                ("task_runs", "agent_runs", "input_files"))]
        assert not any(q.lstrip().startswith(("UPDATE", "INSERT")) for q in queries), queries
        assert sum("parent_task_id IS NOT NULL" in q for q in queries) == 1, queries
        assert not any("FROM input_files" in q for q in queries), queries
        assert len(queries) == 8, queries
        # The callback can read the newly committed row from a separate connection.
        seen = []

        def changed(kind, value):
            if kind == "task":
                seen.append(asyncio.create_task(storage.get_task(value.id)))

        unsubscribe = storage.subscribe_changes(changed)
        await storage.mutate_task_metadata(parent.id, lambda m: {**m, "commit_probe": True})
        assert (await seen[-1]).metadata["commit_probe"] is True
        before = len(seen)

        def fail(_metadata):
            raise RuntimeError("transaction fixture failure")

        with pytest.raises(RuntimeError, match="transaction fixture failure"):
            await storage.mutate_task_metadata(parent.id, fail)
        assert len(seen) == before
        unsubscribe()
    finally:
        if c:
            await c.close()
        if pool:
            await pool.close()
        await admin.execute('SET search_path TO public')
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()
