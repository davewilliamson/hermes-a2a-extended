import asyncio
import sqlite3
from contextlib import closing

import pytest

from async_fixture import running, terminal, wire


def test_admission_immediate_and_single_execution(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch) as (instance, rpc, seen, ready, hold, *_rest):
            params = {"messageId": "fixture", "message": "/approve once"}
            first, duplicate = await asyncio.gather(rpc("tasks/send", **params), rpc("tasks/send", **params))
            assert first == duplicate
            with pytest.raises(wire.Refusal):
                await rpc("tasks/send", messageId="fixture", message="changed payload")
            assert set(first) == {"task_id", "context_id", "received_at"}
            with closing(sqlite3.connect(instance.home / "a2a-async-receipts.sqlite3")) as db:
                assert db.execute("SELECT task,context,received FROM receipt").fetchall() == [tuple(first.values())]
            await asyncio.wait_for(ready.wait(), 1)
            assert not seen[0].allow_gateway_control and seen[0].get_command() is None
            assert (await rpc("tasks/get", id=first["task_id"]))["state"] == "WORKING"
            hold.set()
            result = await terminal(rpc, first)
            assert result["state"] == "COMPLETED" and result["reply"] == "final reply"
            assert len(seen) == len(result["artifacts"]) == 1
    asyncio.run(test())
