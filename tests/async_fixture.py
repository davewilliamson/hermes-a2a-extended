"""Loopback mTLS tests; exercise real Hermes BasePlatformAdapter dispatch."""

import asyncio
import hashlib
import importlib
import json
import secrets
import ssl
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

try:
    import gateway.platforms.base
except ModuleNotFoundError as exc:
    if exc.name not in {"gateway", "gateway.platforms", "gateway.platforms.base"}:
        raise
    pytest.skip("requires pinned Hermes gateway checkout", allow_module_level=True)
from gateway.config import PlatformConfig
from gateway.platform_registry import platform_registry
from hermes_cli.plugins import PluginManager, PluginManifest
from hermes_cli.plugins_context import PluginContext
from gateway.platforms.base import ProcessingOutcome

plugin = importlib.import_module("a2a-async")
adapter = importlib.import_module("a2a-async.adapter")
wire = importlib.import_module("a2a-async.wire")


def config(tmp_path, second_peer=False):
    script = Path(__file__).resolve().parents[1] / "scripts" / "gen_certs.py"
    root = tmp_path / "secrets"
    command = [sys.executable, str(script), "--output", str(root)]
    subprocess.run(command + (["--second-client"] if second_peer else []), check=True)
    credential = root / "peer.env"
    credential.write_text(secrets.token_urlsafe(32))
    ca = str(root / "ca.crt")
    extra = {"port": 0, "ca": ca, "cert": str(root / "server.crt"), "key": str(root / "server.key"),
             "peers": {"fixture": {"token_file": credential, "sha256": hashlib.sha256(
                 ssl.PEM_cert_to_DER_cert((root / "client.crt").read_text())).hexdigest()}}}
    if second_peer:
        credential2 = root / "peer2.env"
        credential2.write_text(secrets.token_urlsafe(32))
        extra["peers"]["fixture2"] = {"token_file": credential2, "sha256": hashlib.sha256(
            ssl.PEM_cert_to_DER_cert((root / "client2.crt").read_text())).hexdigest()}
    return extra, {"ca": ca, "cert": str(root / "client.crt"), "key": str(root / "client.key"), "token_file": credential}


@asynccontextmanager
async def running(tmp_path, monkeypatch, handler=None, extra_attrs=None, second_peer=False):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    policy = {"approvals": {"mode": "manual"}, "security": {"approval": {"transport": "builtin"}}}
    monkeypatch.setattr(adapter, "load_config_readonly", lambda: policy)
    extra, client = config(tmp_path, second_peer)
    manager = PluginManager()
    manager._discovered = True
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: manager)
    plugin.register(PluginContext(PluginManifest(name="a2a-async", source="user"), manager))
    entry = platform_registry.get("a2a-async")
    assert entry is not None
    instance = entry.adapter_factory(PlatformConfig(enabled=True, extra=extra))
    deny = lambda **kwargs: manager.invoke_hook("pre_approval_request", **kwargs)
    if extra_attrs:
        for k, value in extra_attrs.items():
            setattr(instance, k, value)
    seen = []
    ready = asyncio.Event()
    hold = asyncio.Event()

    async def message(event):
        seen.append(event)
        ready.set()
        if handler:
            return await handler(instance, event)
        await hold.wait()
        return "final reply"

    instance.set_message_handler(message)
    assert await instance.connect()
    client["url"] = f"https://127.0.0.1:{instance.server.server_port}"
    monkeypatch.setattr(plugin, "outbound", lambda: {"fixture": client})

    async def rpc(method, **params):
        return await asyncio.to_thread(wire.rpc, client, method, params)

    try:
        yield instance, rpc, seen, ready, hold, deny, policy, client
    finally:
        hold.set()
        await instance.disconnect()
        await asyncio.sleep(0)
        assert instance.server is None and instance.thread is None
        manager.unload("a2a-async")


async def terminal(rpc, ack):
    for _ in range(100):
        result = await rpc("tasks/get", id=ack["task_id"], includeArtifacts=True)
        if result["state"] in adapter.TERMINAL:
            return result
        await asyncio.sleep(.01)
    pytest.fail("task did not reach terminal state")


def decoded(text):
    return json.loads(text)
