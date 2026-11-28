import asyncio
import json
import socket

from async_fixture import running, terminal, wire


def test_disconnect_before_ack_and_subscribe(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch) as (instance, rpc, seen, ready, hold, _, _, client):
            def drop(method, **params):
                body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
                with socket.create_connection(("127.0.0.1", instance.server.server_port), timeout=2) as connection:
                    with wire.tls(client).wrap_socket(connection, server_hostname="127.0.0.1") as secured:
                        head = (f"POST / HTTP/1.0\r\nContent-Length: {len(body)}\r\nAuthorization: Bearer "
                                f"{wire.token(client['token_file'])}\r\n\r\n").encode()
                        secured.sendall(head + body)

            await asyncio.to_thread(drop, "tasks/send", messageId="lost", message="task")
            await asyncio.wait_for(ready.wait(), 2)
            ack = await rpc("tasks/send", messageId="lost", message="task")
            # Every RPC connection closes after its short reply; execution is still held here.
            hold.set()
            await terminal(rpc, ack)
            await asyncio.to_thread(drop, "tasks/subscribe", id=ack["task_id"])
            result = await rpc("tasks/list", contextId=ack["context_id"], includeArtifacts=True)
            assert len(seen) == 1 and result["tasks"][0]["reply"] == "final reply"
    asyncio.run(test())
