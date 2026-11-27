import importlib
import json
import types

import pytest

from mock_a2a_server import DUMMY, STAMP, artifact, server, status, task


def test_register():
    calls = []
    importlib.import_module("a2a-extended").register(types.SimpleNamespace(register_tool=lambda **kw: calls.append(kw)))
    assert [(call["name"], call["toolset"]) for call in calls] == [("a2a_extended_send", "a2a")]


def test_exchange(send):
    with server(legacy=True) as (url, requests, _):
        result = send(url, context_id="ctx-fixture")
    assert (result["outcome"], result["text"]) == ("completed", "reply")
    assert (result["task_id"], result["context_id"]) == ("task-fixture", "ctx-fixture")
    assert (result["received_at"], result["working_at"]) == (STAMP, STAMP)
    assert result["remote_outcome_unknown"] is False
    assert [request[0] for request in requests] == ["GET", "GET", "POST"]
    _, path, headers, body = requests[-1]
    assert path == "/rpc"
    headers = {key.lower(): value for key, value in headers.items()}
    assert headers["authorization"] == "Bearer " + DUMMY
    assert headers["a2a-version"] == "1.0"
    assert headers["content-type"] == "application/json"
    assert body["method"] == "SendStreamingMessage" and body["jsonrpc"] == "2.0"
    assert body["params"] == {"tenant": "fixture-tenant", "message": {
        "role": "ROLE_USER", "messageId": result["message_id"], "contextId": "ctx-fixture",
        "parts": [{"text": "test message", "mediaType": "text/plain"}],
    }}


@pytest.mark.parametrize("state,outcome", [("REJECTED", "failed"), ("INPUT_REQUIRED", "input_required")])
def test_immediate_terminal(send, state, outcome):
    with server(lambda frame, *_: frame(status(state, "remote message"))) as (url, _, _):
        result = send(url)
    assert result["outcome"] == outcome and result["text"] == "remote message"
    assert result["received_at"] is result["working_at"] is None
    assert result["last_remote_state"] == "TASK_STATE_" + state
    assert result["remote_outcome_unknown"] is False


@pytest.mark.parametrize("error,outcome", [(401, "refused"), (403, "refused"), (404, "refused"),
                                           (429, "refused"), (503, "transport_error"),
                                           ({"code": -32601, "message": "no method"}, "refused"),
                                           ({"code": -32602, "message": "bad params"}, "refused")])
def test_pre_sse_errors(send, error, outcome):
    with server(error=error) as (url, requests, _):
        result = send(url)
    assert result["outcome"] == outcome
    assert len([request for request in requests if request[0] == "POST"]) == 1


def test_eof_before_terminal(send):
    with server(lambda frame, *_: frame(task())) as (url, _, _):
        result = send(url)
    assert result["outcome"] == "stream_closed_without_terminal"
    assert result["last_remote_state"] == "TASK_STATE_SUBMITTED"
    assert result["remote_outcome_unknown"] is True


def test_multiline_malformed_and_split_utf8(send):
    def stream(frame, raw, req_id, stop):
        raw(b"data: malformed\r\n\r\ndata: \xff\n\n")
        frame(task())
        first = json.dumps({"jsonrpc": "2.0", "id": req_id})[:-1].encode()
        second = json.dumps({"result": artifact("café")}, ensure_ascii=False)[1:].encode()
        prefix, suffix = second.split(b"\xc3", 1)
        raw(b"data: " + first + b",\r\ndata: " + prefix + b"\xc3")
        stop.wait(0.04)
        raw(suffix + b"\r\n\r\n")
        frame(status("COMPLETED"))

    with server(stream) as (url, _, _):
        result = send(url)
    assert (result["outcome"], result["text"]) == ("completed", "café")
