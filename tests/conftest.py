"""Public API test doubles and worker cleanup assertions."""
import importlib
import sys
import types

import pytest

from mock_a2a_server import DUMMY


@pytest.fixture
def send(tmp_path, monkeypatch):
    client = importlib.import_module("a2a-extended.client")
    package = tmp_path / "hermes_cli"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "urllib_security.py").write_text(
        "def open_credentialed_url(request, *, timeout, opener_factory):\n"
        "    return opener_factory(None).open(request, timeout=timeout)\n"
    )
    config = types.ModuleType("hermes_cli.config")
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config)
    monkeypatch.syspath_prepend(str(tmp_path))
    created = []
    popen = client.subprocess.Popen

    def track(*args, **kwargs):
        proc = popen(*args, **kwargs)
        created.append(proc)
        return proc

    monkeypatch.setattr(client.subprocess, "Popen", track)

    def invoke(url, **kwargs):
        config.load_config_readonly = lambda: {"a2a_agents": {"fixture": {"url": url, "auth": {"token": DUMMY}}}}
        return client.send("fixture", kwargs.pop("message", "test message"), **kwargs)

    yield invoke
    assert all(proc.returncode is not None and proc.stdin.closed and proc.stdout.closed for proc in created)
