import asyncio
import http.client
import socket

from async_fixture import running, wire


def test_card_and_body_errors(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch) as (instance, _, _, _, _, _, _, client):
            def request(method, path, body=None):
                with_conn = http.client.HTTPSConnection("127.0.0.1", instance.server.server_port,
                                                        timeout=2, context=wire.tls(client))
                try:
                    with_conn.request(method, path, body, {"Authorization": "Bearer " + wire.token(client["token_file"])})
                    response = with_conn.getresponse()
                    return response.status, response.read()
                finally:
                    with_conn.close()
            status, body = await asyncio.to_thread(request, "GET", "/.well-known/agent-card.json")
            assert status == 200 and wire.EXTENSION.encode() in body
            assert (await asyncio.to_thread(request, "POST", "/", b"{"))[0] == 200
            assert (await asyncio.to_thread(request, "POST", "/", b"x" * (wire.MAX_BODY + 1)))[0] == 413
    asyncio.run(test())


def test_disconnect_closes_stalled_tls_workers(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch) as (instance, *_):
            server = instance.server
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as stalled:
                for _ in range(100):
                    with server.connection_lock:
                        if server.connections:
                            break
                    await asyncio.sleep(.01)
                assert server.connections
                await instance.disconnect()
                assert not server.connections and stalled.recv(1) == b""
    asyncio.run(test())
