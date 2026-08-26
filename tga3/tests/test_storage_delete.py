from pathlib import Path
from uuid import uuid4

import pytest

from tga3.storage import PostgresStorage


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.transaction_entered = False

    def transaction(self):
        connection = self

        class Transaction(AsyncContext):
            async def __aenter__(self):
                connection.transaction_entered = True
                return await super().__aenter__()

        return Transaction(self)

    async def execute(self, query, *args):
        assert self.transaction_entered
        self.calls.append((" ".join(query.split()), args))
        if query.startswith("DELETE FROM task_runs"):
            return "DELETE 1"
        return "OK"


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return AsyncContext(self.connection)


@pytest.mark.asyncio
async def test_postgres_delete_removes_artifact_links_inside_transaction():
    connection = FakeConnection()
    storage = PostgresStorage(FakePool(connection))
    task_id = uuid4()

    await storage.delete_task(task_id)

    queries = [query for query, _args in connection.calls]
    link_delete = next(index for index, query in enumerate(queries) if "DELETE FROM finding_artifact_links" in query)
    task_delete = next(index for index, query in enumerate(queries) if "DELETE FROM task_runs" in query)
    assert link_delete < task_delete
    assert connection.calls[link_delete][1] == (task_id,)


def test_artifact_link_foreign_key_cascades_on_artifact_delete():
    schema = (Path(__file__).parents[1] / "schema.sql").read_text(encoding="utf-8")
    assert "artifact_id UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE" in schema
