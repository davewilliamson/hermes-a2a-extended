"""Unit tests for the pure validation / redaction / redirect policy in a2a-extended.policy.

These exercise the parent-process decision functions directly (no subprocess, no
hermes_cli), which the integration suite only reaches through the spawned network
worker and therefore leaves invisible to plain coverage measurement.
"""

import importlib
import io
import sys
import types
from urllib.request import Request

import pytest

policy = importlib.import_module("a2a-extended.policy")
Refused = policy.Refused


@pytest.fixture
def config(monkeypatch):
    mod = sys.modules.get("hermes_cli.config")
    if mod is None:
        mod = types.ModuleType("hermes_cli.config")
        sys.modules["hermes_cli.config"] = mod

    def set_config(mapping):
        monkeypatch.setattr(mod, "load_config_readonly", lambda: mapping, raising=False)

    return set_config


# ---------------------------------------------------------------------------
# origin()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,scheme,host,port", [
    ("http://example.com/x", "http", "example.com", 80),
    ("https://example.com/x", "https", "example.com", 443),
    ("https://example.com:9443/x", "https", "example.com", 9443),
    ("HTTP://EXAMPLE.com:8080/x", "http", "example.com", 8080),
])
def test_origin_valid(url, scheme, host, port):
    assert policy.origin(url) == (scheme, host, port)


@pytest.mark.parametrize("url", [
    "",
    "not a url",
    "http://",
    "ftp://example.com",
    "http:///path",
    "http://user@example.com/x",
    "http://exa mple.com/x",
    "http://example.com\\x",
    "http://example.com/\x00",
])
def test_origin_refuses(url):
    with pytest.raises(Refused):
        policy.origin(url)


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------

def test_check_same_origin_is_allowed():
    assert policy.check("https://example.com/rpc", ("https", "example.com", 443)) is None


def test_check_cross_origin_refused():
    with pytest.raises(Refused):
        policy.check("https://other.example.com/rpc", ("https", "example.com", 443))


# ---------------------------------------------------------------------------
# redact()
# ---------------------------------------------------------------------------

def test_redact_replaces_token():
    assert policy.redact("reply contains SECRET here", "SECRET") == "reply contains [REDACTED] here"


def test_redact_empty_token_is_identity():
    assert policy.redact("plain text", "") == "plain text"


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------

def test_resolve_valid(config):
    config({"a2a_agents": {"peer": {"url": "https://example.com", "auth": {"token": "tok"},
                                    "tenant": "tenant-a"}}})
    url, token, tenant = policy.resolve("peer")
    assert (url, token, tenant) == ("https://example.com", "tok", "tenant-a")


def test_resolve_missing_peer(config):
    config({"a2a_agents": {}})
    with pytest.raises(Refused):
        policy.resolve("peer")


def test_resolve_non_dict_entry(config):
    config({"a2a_agents": {"peer": "not-a-dict"}})
    with pytest.raises(Refused):
        policy.resolve("peer")


@pytest.mark.parametrize("token", ["bad token with space", "caf\xe9", "\x01control"])
def test_resolve_rejects_unprintable_token(config, token):
    config({"a2a_agents": {"peer": {"url": "https://example.com", "auth": {"token": token}}}})
    with pytest.raises(Refused):
        policy.resolve("peer")


@pytest.mark.parametrize("url", ["https://example.com/x?q=1", "https://example.com/x#frag"])
def test_resolve_rejects_query_or_fragment(config, url):
    config({"a2a_agents": {"peer": {"url": url, "auth": {"token": "tok"}}}})
    with pytest.raises(Refused):
        policy.resolve("peer")


# ---------------------------------------------------------------------------
# SameOriginRedirect
# ---------------------------------------------------------------------------

def test_redirect_cross_origin_is_refused():
    handler = policy.SameOriginRedirect(("https", "example.com", 443))
    req = Request("https://example.com/rpc", data=b"{}", method="POST")
    with pytest.raises(Refused):
        handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://evil.example.com/rpc")


@pytest.mark.parametrize("code", [307, 308])
def test_redirect_preserves_post_method_and_body(code):
    handler = policy.SameOriginRedirect(("https", "example.com", 443))
    req = Request("https://example.com/rpc", data=b'{"x":1}', method="POST")
    out = handler.redirect_request(req, io.BytesIO(), code, "Moved", {}, "https://example.com/rpc2")
    assert out.get_method() == "POST"
    assert out.data == b'{"x":1}'


@pytest.mark.parametrize("code", [301, 302, 303])
def test_redirect_post_that_would_change_method_is_refused(code):
    handler = policy.SameOriginRedirect(("https", "example.com", 443))
    req = Request("https://example.com/rpc", data=b"{}", method="POST")
    with pytest.raises(Refused):
        handler.redirect_request(req, io.BytesIO(), code, "Found", {}, "https://example.com/rpc2")
