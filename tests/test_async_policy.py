import asyncio
import secrets
import ssl
import subprocess
import sys
from types import SimpleNamespace

import pytest

from async_fixture import running, terminal, wire
from tools import approval, approval_context


def test_gate_refuses_startup_and_admission(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch) as (instance, rpc, _, _, _, _, policy, _):
            for section, field, value in ((policy["approvals"], "mode", "off"),
                                          (policy["security"]["approval"], "transport", "external")):
                previous = section[field]
                section[field] = value
                with pytest.raises(wire.Refusal):
                    await rpc("tasks/send", messageId="unsafe", message="test")
                assert not await instance.connect()
                section[field] = previous
            monkeypatch.setenv("HERMES_YOLO_MODE", "0")
            with pytest.raises(wire.Refusal):
                await rpc("tasks/send", messageId="unsafe", message="test")
            assert not await instance.connect()
            monkeypatch.delenv("HERMES_YOLO_MODE")
            assert not instance.rows
    asyncio.run(test())


def test_certificate_token_rotation_and_cross_profile(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch) as (instance, rpc, _, _, _, _, _, client):
            alternate = client["token_file"].with_name("old.env")
            alternate.write_text(client["token_file"].read_text())
            client["token_file"].write_text(secrets.token_urlsafe(32))
            with pytest.raises(wire.Refusal):
                await asyncio.to_thread(wire.rpc, {**client, "token_file": alternate}, "tasks/list", {})
            assert await rpc("tasks/list") == {"tasks": []}
            bad_cert = {**client, "cert": instance.config.extra["cert"], "key": instance.config.extra["key"]}
            with pytest.raises((OSError, ssl.SSLError)):
                await asyncio.to_thread(wire.rpc, bad_cert, "tasks/list", {})
            runner = SimpleNamespace(_profile_name_for_source=lambda source, adapter_profile: "not-the-owner")
            monkeypatch.setattr(instance, "gateway_runner", runner)
            with pytest.raises(wire.Refusal):
                await rpc("tasks/send", messageId="routed", message="test")
            assert not instance.rows
    asyncio.run(test())


def test_per_peer_ownership_and_token_certificate_pairing(tmp_path, monkeypatch):
    async def test():
        async with running(tmp_path, monkeypatch, second_peer=True) as (instance, rpc, _, _, hold, _, _, client):
            cert_root = client["token_file"].parent
            other = {**client, "token_file": cert_root / "peer2.env", "cert": str(cert_root / "client2.crt"),
                     "key": str(cert_root / "client2.key")}
            ack = await rpc("tasks/send", messageId="private", message="test")
            assert await asyncio.to_thread(wire.rpc, other, "tasks/list", {"messageId": "private"}) == {"tasks": []}
            for method in ("tasks/get", "tasks/cancel", "tasks/subscribe"):
                assert await asyncio.to_thread(wire.rpc, other, method, {"id": ack["task_id"]}) == {"state": "unknown"}
            wrong_pair = {**other, "token_file": client["token_file"]}
            with pytest.raises(wire.Refusal, match="refused"):
                await asyncio.to_thread(wire.rpc, wrong_pair, "tasks/list", {})
            hold.set()
    asyncio.run(test())


@pytest.mark.parametrize("hook_enabled", [True, False], ids=["deny-before-notifier", "negative-control"])
def test_real_approval_denied_before_notifier(tmp_path, monkeypatch, hook_enabled):
    """With the hook removed, the notifier's attempted approval runs the sentinel."""
    monkeypatch.setattr(approval_context, "_get_approval_config", lambda: {"mode": "manual", "timeout": 1})
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    if not hook_enabled:
        monkeypatch.setattr("a2a-async.adapter.resolve_gateway_approval", lambda *args, **kwargs: 0)
    sentinel = tmp_path / "executed"
    approvals_at_notifier = []

    async def agent(instance, event):
        key = instance._event_session_key(event)

        def guard_and_execute():
            context = approval_context.set_current_session_key(key)
            try:
                approval.register_gateway_notify(key, lambda _: approvals_at_notifier.append(
                    approval.resolve_gateway_approval(key, "once")))
                verdict = approval.check_all_command_guards("chmod 777 fixture", "local")
                if verdict.get("approved"):
                    subprocess.run([sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()",
                                    str(sentinel)], check=True)
                    return "executed"
                return "denied"
            finally:
                approval.unregister_gateway_notify(key)
                approval_context._approval_session_key.reset(context)

        return await asyncio.to_thread(guard_and_execute)

    async def test():
        async with running(tmp_path, monkeypatch, handler=agent) as (_, rpc, *_):
            ack = await rpc("tasks/send", messageId="guard", message="request approval")
            result = await terminal(rpc, ack)
            assert result["reply"] == ("denied" if hook_enabled else "executed")
            assert approvals_at_notifier == ([0] if hook_enabled else [1])
            assert sentinel.exists() is not hook_enabled
    asyncio.run(test())
