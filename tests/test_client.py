import asyncio

from async_fixture import decoded, plugin, running, terminal


def test_dispatch_status_and_ack_loss(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch) as (_, rpc, seen, _, hold, *_):
            real = plugin.rpc
            attempts = []

            def lose_first(config, method, params):
                response = real(config, method, params)
                if method == "tasks/send":
                    attempts.append((params["messageId"], response))
                    if len(attempts) == 1:
                        raise OSError("ack lost")
                return response

            monkeypatch.setattr(plugin, "rpc", lose_first)
            ack = decoded(await asyncio.to_thread(plugin.dispatch, {"peer": "fixture", "message": "sample"}))
            assert ack["outcome"] == "admitted" and attempts[0] == attempts[1]
            hold.set()
            await terminal(rpc, ack)
            for key in ("task_id", "context_id", "submission_id"):
                result = decoded(await asyncio.to_thread(plugin.status, {key: ack[key]}))
                assert result["state"] == "COMPLETED" and result["reply"] == "final reply"
            assert len(seen) == 1
            assert decoded(await asyncio.to_thread(plugin.status, {"task_id": "missing"})) == {"state": "unknown"}
    asyncio.run(test())


def test_status_recovers_receipt_after_both_acks_lost(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch) as (_, rpc, seen, _, hold, *_):
            real = plugin.rpc
            lost = []

            def lose_acks(config, method, params):
                result = real(config, method, params)
                if method == "tasks/send":
                    lost.append(result)
                    raise OSError("ack lost")
                return result

            monkeypatch.setattr(plugin, "rpc", lose_acks)
            status = decoded(await asyncio.to_thread(plugin.dispatch, {"peer": "fixture", "message": "sample"}))
            assert status == {"outcome": "pending", "submission_id": status["submission_id"]}
            assert len(lost) == 2 and lost[0] == lost[1]
            hold.set()
            await terminal(rpc, lost[0])
            result = decoded(await asyncio.to_thread(plugin.status, {"submission_id": status["submission_id"]}))
            assert result["state"] == "COMPLETED" and result["reply"] == "final reply"
            assert len(seen) == 1
    asyncio.run(test())
