import json

import pytest

from mock_a2a_server import DUMMY, artifact, server, status


@pytest.mark.parametrize("source", ["artifact", "message", "error"])
def test_redact_bearer_from_outbound_text_and_remote_output(send, capsys, source):
    def stream(frame, *_):
        if source == "artifact":
            frame(artifact("echo " + DUMMY))
            frame(status("COMPLETED"))
        else:
            frame(status("INPUT_REQUIRED", "echo " + DUMMY))

    error = {"code": -32602, "message": "echo " + DUMMY} if source == "error" else None
    with server(stream, error=error) as (url, requests, _):
        result = send(url, message="outbound " + DUMMY)
    assert result["text"] == "echo [REDACTED]"
    assert DUMMY not in json.dumps(result)
    assert requests[-1][3]["params"]["message"]["parts"][0]["text"] == "outbound [REDACTED]"
    captured = capsys.readouterr()
    assert DUMMY not in captured.out + captured.err
