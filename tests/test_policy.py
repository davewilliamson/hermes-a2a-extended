import pytest

from mock_a2a_server import DUMMY, server


@pytest.mark.parametrize("kind", ["endpoint", "redirect"])
def test_never_contact_cross_origin(send, kind):
    with server() as (other, received, _):
        with server(**{kind: other + "/card"}) as (url, requests, _):
            result = send(url)
    assert result["outcome"] == "refused" and "Cross-origin" in result["text"]
    assert result["remote_outcome_unknown"] is False
    assert len(requests) == 1 and not received


def test_same_origin_discovery_redirect(send):
    with server(redirect="/card") as (url, requests, _):
        result = send(url)
    assert result["outcome"] == "completed"
    assert requests[1][1] == "/card"
    for _, _, headers, _ in requests:
        assert {k.lower(): v for k, v in headers.items()}["authorization"] == "Bearer " + DUMMY


def test_nonstreaming_peer_refused(send):
    with server(streaming=False) as (url, requests, _):
        result = send(url)
    assert result["outcome"] == "refused"
    assert [request[0] for request in requests] == ["GET"]
