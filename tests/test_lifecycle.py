import asyncio

from async_fixture import running, terminal
from gateway.platforms.base import ProcessingOutcome
from gateway.platforms.event import MessageEvent


def test_queue_cancel_and_deadline_race(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch, extra_attrs={"concurrency": 1, "deadline_s": .5}) as (
                instance, rpc, seen, ready, hold, *_):
            first = await rpc("tasks/send", messageId="one", message="first")
            await ready.wait()
            second = await rpc("tasks/send", messageId="two", message="queued")
            assert (await rpc("tasks/get", id=second["task_id"]))["state"] == "SUBMITTED"
            await instance.send(first["context_id"], "interim")
            assert (await rpc("tasks/cancel", id=second["task_id"])) == {"cancel": "observation_only"}
            assert (await terminal(rpc, second))["state"] == "CANCELED"
            assert (await terminal(rpc, first))["state"] == "TIMED_OUT"
            hold.set()
            await asyncio.sleep(.05)
            assert (await terminal(rpc, first))["state"] == "TIMED_OUT" and len(seen) == 1
    asyncio.run(test())


def test_dispatch_exception_eviction_and_restart(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch, extra_attrs={"retain": 1}) as (instance, rpc, *_):
            dispatch = instance.handle_message

            async def broken(event):
                raise RuntimeError("synthetic dispatch failure")

            instance.handle_message = broken
            old = await rpc("tasks/send", messageId="broken", message="first")
            result = await terminal(rpc, old)
            assert result["state"] == "FAILED" and result["reason"] == "dispatch failed"
            latest = await rpc("tasks/send", messageId="last", message="second")
            await terminal(rpc, latest)
            assert await rpc("tasks/get", id=old["task_id"]) == {"state": "unknown"}
            instance.handle_message = dispatch
            await instance.disconnect()
            assert await instance.connect()
            # A newly attached listener cannot reconstruct a previous generation's record.
            assert await instance.request("fixture", "tasks/get", {"id": latest["task_id"]}) == {"state": "unknown"}
    asyncio.run(test())


def test_streamed_fallback_and_empty_are_not_fabricated(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch) as (instance, rpc, seen, ready, hold, *_):
            first = await rpc("tasks/send", messageId="streamed", message="test")
            await ready.wait()
            assert isinstance(seen[0], MessageEvent)
            seen[0]._streamed_final_response = "mirrored"  # no stream feature is enabled in the adapter
            await instance.on_processing_complete(seen[0], ProcessingOutcome.SUCCESS)
            assert (await terminal(rpc, first))["reply"] == "mirrored"
            ready.clear()
            second = await rpc("tasks/send", messageId="empty", message="test")
            await ready.wait()
            await instance.on_processing_complete(seen[1], ProcessingOutcome.SUCCESS)
            empty = await terminal(rpc, second)
            assert empty["state"] == "COMPLETED" and empty["reason"] == "empty reply" and "reply" not in empty
            hold.set()
    asyncio.run(test())
