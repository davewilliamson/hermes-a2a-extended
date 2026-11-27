from time import monotonic

import pytest

from mock_a2a_server import keepalive, server, status, task


@pytest.mark.parametrize("working", [True, False])
def test_overall_timeout_despite_keepalives(send, working):
    def stream(frame, raw, _id, stop):
        frame(task())
        if working:
            frame(status("WORKING"))
        keepalive(raw, stop)

    started = monotonic()
    with server(stream) as (url, requests, disconnected):
        result = send(url, liveness_timeout_s=0.24, _silence=0.6)
        assert disconnected.wait(1)
    assert 0.22 <= monotonic() - started < 2
    assert result["outcome"] == "local_timeout" and result["timeout_phase"] == "overall"
    assert result["last_remote_state"] == ("TASK_STATE_WORKING" if working else "TASK_STATE_SUBMITTED")
    assert result["remote_outcome_unknown"] is True
    assert len([request for request in requests if request[0] == "POST"]) == 1


def test_missing_ack_despite_keepalives(send):
    with server(lambda _, raw, _id, stop: keepalive(raw, stop)) as (url, _, disconnected):
        result = send(url, liveness_timeout_s=2, _ack=0.20, _silence=0.6)
        assert disconnected.wait(1)
    assert result["outcome"] == "local_timeout" and result["timeout_phase"] == "acknowledgement"
    assert result["last_remote_state"] is None and result["remote_outcome_unknown"] is True


def test_delayed_ack_within_deadline(send):
    def stream(frame, raw, _id, stop):
        raw(b": keepalive\n\n")
        stop.wait(0.06)
        frame(task())
        frame(status("WORKING"))
        frame(status("COMPLETED"))

    with server(stream) as (url, _, _):
        result = send(url, _ack=0.5)
    assert result["outcome"] == "completed"


def test_transport_silence(send):
    def stream(frame, raw, _id, stop):
        frame(task())
        frame(status("WORKING"))
        if not stop.wait(0.65):
            keepalive(raw, stop)

    with server(stream) as (url, _, disconnected):
        result = send(url, _silence=0.4)
        assert disconnected.wait(1.5)
    assert result["outcome"] == "transport_error" and result["timeout_phase"] is None
    assert result["last_remote_state"] == "TASK_STATE_WORKING" and result["remote_outcome_unknown"] is True
