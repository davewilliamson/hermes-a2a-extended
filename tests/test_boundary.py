"""Regression alarms for the Hermes seams deliberately mirrored by v2."""

import ast
import inspect
import subprocess
from pathlib import Path

from async_fixture import adapter
from gateway.platforms.base import BasePlatformAdapter
from gateway.platforms.event import MessageEvent

PIN = "8fc0374447313e276f6b9a2764bfc750190c966a"


def test_pinned_hermes_boundary():
    root = Path(inspect.getfile(BasePlatformAdapter)).resolve().parents[2]
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == PIN, "Revalidate all mirrored seams before changing the Hermes pin"
    fields = MessageEvent.__dataclass_fields__
    assert fields["allow_gateway_control"].default is True
    assert fields["_gateway_accepted"].default is False
    assert "message_id" in fields
    reference = ast.parse((root / "plugins/platforms/a2a/adapter.py").read_text())
    candidate = ast.parse(inspect.getsource(adapter))
    for expected in ("getattr(event, '_streamed_final_response', '')", "getattr(event, 'message_id', '')"):
        node = ast.dump(ast.parse(expected, mode="eval").body)
        assert any(ast.dump(n) == node for n in ast.walk(reference))
        assert any(ast.dump(n) == node for n in ast.walk(candidate))
    assert "profile_route_rejected" in inspect.getsource(BasePlatformAdapter.build_source)
    assert "_gateway_accepted" in inspect.getsource(BasePlatformAdapter.handle_message)
    assert "self._event_session_key(event)" in inspect.getsource(BasePlatformAdapter.handle_message)
    assert callable(BasePlatformAdapter._event_session_key)
    for method in ("_mark_connected", "_mark_disconnected"):
        assert callable(getattr(BasePlatformAdapter, method))
        assert f"self.{method}" in (root / "plugins/platforms/a2a/adapter.py").read_text()
