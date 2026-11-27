"""A2A v1.0 discovery and SSE decoding with a cancellable urllib worker."""

import base64
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
from time import monotonic
from urllib.error import HTTPError
from uuid import uuid4

from .liveness import Deadlines
from .policy import Refused, check, open_peer, origin, redact, resolve


_LIMIT = 1024 * 1024
_TERMINAL = {"TASK_STATE_" + state: outcome for state, outcome in (
    ("COMPLETED", "completed"), ("FAILED", "failed"), ("CANCELED", "failed"),
    ("REJECTED", "failed"), ("INPUT_REQUIRED", "input_required"),
)}
_PENDING = {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}


def _emit(kind, value=None):
    print(json.dumps([kind, value]), flush=True)


def _read(response, emit, stream=False):
    body = bytearray()
    while True:
        data = response.read1(8192)
        if not data:
            return body
        if stream:
            emit("data", base64.b64encode(data).decode("ascii"))
        else:
            emit("activity")
            body.extend(data)
            if len(body) > _LIMIT:
                raise ValueError("Oversized JSON response")


def _network(url, token, tenant, message, req_id, emit):
    trusted = origin(url)
    card = None

    def activity():
        emit("activity")

    for index, path in enumerate(("agent-card.json", "agent.json")):
        try:
            with open_peer(url.rstrip("/") + "/.well-known/" + path, trusted, token,
                           activity=activity) as response:
                card = json.loads(_read(response, emit))
            break
        except HTTPError as error:
            with error:
                if index or error.code != 404:
                    raise
    if not isinstance(card, dict) or not (card.get("capabilities") or {}).get("streaming"):
        raise Refused("Peer does not advertise streaming.")
    interface = next((item for item in card.get("supportedInterfaces", [])
                      if isinstance(item, dict) and item.get("protocolBinding") == "JSONRPC"
                      and item.get("protocolVersion") == "1.0" and item.get("url")), None)
    if interface is None:
        raise Refused("Peer does not advertise an A2A 1.0 JSON-RPC interface.")
    endpoint = interface["url"]
    check(endpoint, trusted)
    params = {"message": message}
    tenant = interface.get("tenant") or tenant
    if tenant:
        params["tenant"] = tenant
    body = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": "SendStreamingMessage",
                       "params": params}).encode("utf-8")
    emit("dispatch")
    with open_peer(endpoint, trusted, token, activity=activity, data=body) as response:
        emit("headers")
        if response.headers.get_content_type() == "text/event-stream":
            _read(response, emit, stream=True)
            emit("eof")
        else:
            raw = _read(response, emit)
            try:
                envelope = json.loads(raw)
            except (ValueError, UnicodeError):
                envelope = None
            emit("json", envelope)


def _worker():
    # Only stdin carries credentials. Never expose diagnostics via child stderr.
    try:
        paths, args = json.load(sys.stdin)
        sys.path.extend(paths)
        _network(*args, _emit)
    except Refused as error:
        _emit("refused", str(error))
    except HTTPError as error:
        with error:
            _emit("http", error.code)
    except Exception:
        _emit("transport", "Peer connection or response failed.")


def _events(process, payload, deadline):
    """Pump both pipes without waiting beyond the currently applicable deadline."""
    pending = memoryview(payload)
    buffer = bytearray()
    with selectors.DefaultSelector() as selector:
        for pipe, event in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, event)
        while True:
            end, phase = deadline.next()
            remaining = end - monotonic()
            if remaining <= 0:
                yield "timeout", phase
                return
            for key, event in selector.select(remaining):
                if monotonic() >= deadline.next()[0]:
                    break
                if event == selectors.EVENT_WRITE:
                    pending = pending[os.write(key.fd, pending[:65536]):]
                    if not pending:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                data = os.read(key.fd, 65536)
                if not data:
                    yield "transport", "Network worker closed unexpectedly."
                    return
                buffer.extend(data)
                while (index := buffer.find(b"\n")) >= 0:
                    if monotonic() >= deadline.next()[0]:
                        break
                    line = bytes(buffer[:index])
                    del buffer[:index + 1]
                    yield json.loads(line)


class SSE:
    """Assemble complete events as bytes, then decode split UTF-8 once."""

    def __init__(self):
        self.line = bytearray()
        self.data = []
        self.size = 0
        self.skip_lf = False
        self.first = True

    def feed(self, chunk):
        for part in re.split(rb"([\r\n])", chunk):
            if not part:
                continue
            skip = self.skip_lf and part == b"\n"
            self.skip_lf = part == b"\r"
            if skip:
                continue
            if part not in (b"\r", b"\n"):
                self.line.extend(part)
                if len(self.line) + self.size > _LIMIT:
                    raise ValueError("Oversized SSE event.")
                continue
            line = bytes(self.line)
            self.line.clear()
            if self.first:
                self.first = False
                if line.startswith(b"\xef\xbb\xbf"):
                    line = line[3:]
            if line.startswith(b"data:"):
                value = line[5:]
                self.data.append(value[1:] if value.startswith(b" ") else value)
                self.size += len(value) + 1
            elif not line and self.data:
                data = b"\n".join(self.data)
                self.data.clear()
                self.size = 0
                try:
                    yield json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeError):
                    continue


def _text(message):
    parts = message.get("parts") if isinstance(message, dict) else None
    return "\n".join(part["text"] for part in parts or [] if isinstance(part, dict)
                     and isinstance(part.get("text"), str)).strip() if isinstance(parts, list) else ""


def _timestamp(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    return None


class Reply:
    def __init__(self, result, req_id):
        self.result = result
        self.req_id = req_id
        self.artifacts = []
        self.artifact_size = 0

    def event(self, frame, deadline, now):
        if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0" or frame.get("id") != self.req_id:
            return False
        result = self.result
        error = frame.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            result.update(outcome="refused" if code in (-32601, -32602) else "failed",
                          remote_outcome_unknown=bool(result["last_remote_state"]),
                          text=str(error.get("message", "Peer returned a JSON-RPC error.")))
            return True
        payload = frame.get("result")
        if not isinstance(payload, dict):
            return False
        for key, id_key in (("task", "id"), ("statusUpdate", "taskId"), ("artifactUpdate", "taskId")):
            item = payload.get(key)
            if not isinstance(item, dict):
                continue
            task, context = item.get(id_key), item.get("contextId")
            if not isinstance(task, str) or not isinstance(context, str):
                continue
            if result["task_id"] and (task != result["task_id"] or context != result["context_id"]):
                continue
            if key == "artifactUpdate":
                text = _text(item.get("artifact"))
                if text and self.artifact_size + len(text) <= _LIMIT:
                    self.artifacts.append(text)
                    self.artifact_size += len(text)
                result.update(task_id=task, context_id=context)
                continue
            status = item.get("status")
            state = status.get("state") if isinstance(status, dict) else None
            if not isinstance(state, str) or state not in _PENDING and state not in _TERMINAL:
                continue
            if state == "TASK_STATE_SUBMITTED" and deadline.working:
                continue
            result.update(task_id=task, context_id=context, last_remote_state=state)
            stamp = _timestamp(status.get("timestamp"))
            if state == "TASK_STATE_SUBMITTED" and not deadline.acknowledged:
                result["received_at"] = stamp
            if state == "TASK_STATE_WORKING" and not deadline.working:
                result["working_at"] = stamp
            deadline.state(now, state)
            if state in _TERMINAL:
                text = _text(status.get("message"))
                artifacts = item.get("artifacts")
                if not text and isinstance(artifacts, list):
                    text = "\n".join(filter(None, map(_text, artifacts)))
                result.update(outcome=_TERMINAL[state], remote_outcome_unknown=False,
                              text=text or "\n".join(self.artifacts))
                return True
        return False


def _drive(process, payload, deadline, result, req_id):
    parser, reply = SSE(), Reply(result, req_id)
    events = _events(process, payload, deadline)
    try:
        for kind, value in events:
            now = monotonic()
            if kind in ("headers", "activity", "data"):
                deadline.activity(now)
            if kind == "dispatch":
                deadline.dispatch(now)
                result["remote_outcome_unknown"] = True
            elif kind == "headers":
                deadline.headers(now)
            elif kind == "data":
                if any(reply.event(frame, deadline, now) for frame in parser.feed(base64.b64decode(value))):
                    return
            elif kind == "json":
                if not reply.event(value, deadline, now):
                    result.update(outcome="transport_error", text="Expected an A2A SSE response or JSON-RPC error.")
                return
            elif kind == "timeout":
                if value == "discovery":
                    result.update(outcome="transport_error", text="Peer discovery deadline expired.")
                elif value == "transport":
                    result.update(outcome="transport_error", text="Peer transport was silent.")
                else:
                    result.update(outcome="local_timeout", timeout_phase=value, text="Local deadline expired.")
                return
            elif kind == "http":
                refused = 400 <= value < 500 or 300 <= value < 400
                result.update(outcome="refused" if refused else "transport_error", text=f"Peer returned HTTP {value}.")
                if refused:
                    result["remote_outcome_unknown"] = False
                return
            elif kind == "refused":
                result.update(outcome="refused", text=value, remote_outcome_unknown=False)
                return
            elif kind == "eof":
                result.update(outcome="stream_closed_without_terminal", text="Peer closed the stream before a terminal state.")
                return
            elif kind == "transport":
                result.update(outcome="transport_error", text=value)
                return
    finally:
        events.close()


def send(peer, message, context_id=None, liveness_timeout_s=120, *, _ack=30, _silence=17):
    started = monotonic()
    message_id, req_id = uuid4().hex, uuid4().hex
    result = dict(outcome="refused", task_id=None, context_id=None, message_id=message_id,
                  received_at=None, working_at=None, timeout_phase=None, last_remote_state=None,
                  remote_outcome_unknown=False, elapsed_s=0, text="")
    token = ""
    try:
        needed = (peer, message) if context_id is None else (peer, message, context_id)
        if not all(isinstance(value, str) for value in needed):
            raise Refused("Peer, message and context ID must be strings.")
        if isinstance(liveness_timeout_s, bool) or not isinstance(liveness_timeout_s, (int, float)) or not (
            math.isfinite(liveness_timeout_s) and liveness_timeout_s > 0
        ):
            raise Refused("liveness_timeout_s must be a finite positive number.")
        url, token, tenant = resolve(peer)
        msg = {"role": "ROLE_USER", "parts": [{"text": redact(message, token), "mediaType": "text/plain"}],
               "messageId": message_id}
        if context_id:
            msg["contextId"] = redact(context_id, token)
        payload = json.dumps([sys.path, [url, token, tenant, msg, req_id]]).encode("utf-8")
        # urllib's blocking DNS/header reads are not externally cancellable. A
        # disposable process lets the parent enforce wall-clock deadlines and
        # guarantees the OS closes its sockets on termination, with no reader thread.
        directory = Path(__file__).resolve().parent
        command = "import importlib,sys; importlib.import_module(sys.argv[1] + '.client')._worker()"
        with subprocess.Popen([sys.executable, "-c", command, directory.name], cwd=directory.parent,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            try:
                _drive(process, payload, Deadlines(monotonic(), liveness_timeout_s, _ack, _silence), result, req_id)
            finally:
                if process.poll() is None:
                    process.kill()
    except Refused as error:
        result.update(outcome="refused", text=str(error))
    except (OSError, ValueError, TypeError, AttributeError, ImportError):
        result.update(outcome="transport_error", text="Unable to configure or communicate with the peer.")
    result["elapsed_s"] = round(monotonic() - started, 3)
    return {key: redact(value, token) if isinstance(value, str) else value for key, value in result.items()}
